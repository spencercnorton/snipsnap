// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#include "portalscreenshotrequest.h"
#include "utils/capturetrace.h"

#if defined(Q_OS_UNIX) && !defined(Q_OS_MACOS)

#include <QDBusError>
#include <QDBusMessage>
#include <QDBusObjectPath>
#include <QDBusPendingCallWatcher>
#include <QDBusPendingReply>
#include <QFileInfo>
#include <QUrl>
#include <QUuid>
#include <utility>

namespace {

constexpr auto PortalPath = "/org/freedesktop/portal/desktop";
constexpr auto ScreenshotInterface = "org.freedesktop.portal.Screenshot";
constexpr auto RequestInterface = "org.freedesktop.portal.Request";
constexpr auto RequestPathPrefix = "/org/freedesktop/portal/desktop/request/";
constexpr int MinimumTimeoutMs = 10;
constexpr int MaximumTimeoutMs = 600000;

} // namespace

PortalScreenshotRequest::PortalScreenshotRequest(QObject* parent)
  : PortalScreenshotRequest(QDBusConnection::sessionBus(),
                            QStringLiteral("org.freedesktop.portal.Desktop"),
                            parent)
{}

PortalScreenshotRequest::PortalScreenshotRequest(
  const QDBusConnection& connection,
  QString service,
  QObject* parent)
  : QObject(parent)
  , m_connection(connection)
  , m_service(std::move(service))
{
    m_timeout.setSingleShot(true);
    connect(
      &m_timeout, &QTimer::timeout, this, &PortalScreenshotRequest::onTimeout);
}

PortalScreenshotRequest::~PortalScreenshotRequest()
{
    m_timeout.stop();
    disconnectResponses();
    closeRequestAsync();
}

bool PortalScreenshotRequest::start(const QString& parentWindow,
                                    int timeoutMs,
                                    bool interactive)
{
    if (isBusy()) {
        emit busyRejected();
        return false;
    }

    if (!m_connection.isConnected()) {
        complete(Result::TransportError,
                 QUrl(),
                 tr("The D-Bus session connection is not available"));
        return true;
    }
    if (m_service.isEmpty()) {
        complete(Result::TransportError,
                 QUrl(),
                 tr("The desktop portal service name is invalid"));
        return true;
    }

    const QString token =
      QUuid::createUuid().toString(QUuid::Id128).replace('-', '_');
    m_predictedPath = makeRequestPath(token);
    m_requestPath = m_predictedPath;
    if (!connectResponse(m_predictedPath)) {
        complete(Result::TransportError,
                 QUrl(),
                 tr("Unable to subscribe to the desktop portal response"));
        return true;
    }

    QVariantMap options;
    options.insert(QStringLiteral("handle_token"), token);
    options.insert(QStringLiteral("interactive"), interactive);

    QDBusMessage message =
      QDBusMessage::createMethodCall(m_service,
                                     QLatin1String(PortalPath),
                                     QLatin1String(ScreenshotInterface),
                                     QStringLiteral("Screenshot"));
    message << parentWindow << options;

    m_state = State::AwaitingMethodReply;
    m_timeout.start(qBound(MinimumTimeoutMs, timeoutMs, MaximumTimeoutMs));
    CaptureTrace::mark(
      QStringLiteral("portal_method_call_dispatched"),
      { { QStringLiteral("timeout_ms"),
          qBound(MinimumTimeoutMs, timeoutMs, MaximumTimeoutMs) },
        { QStringLiteral("interactive"), interactive } });
    m_callWatcher =
      new QDBusPendingCallWatcher(m_connection.asyncCall(message), this);
    connect(m_callWatcher,
            &QDBusPendingCallWatcher::finished,
            this,
            &PortalScreenshotRequest::onScreenshotCallFinished);
    return true;
}

bool PortalScreenshotRequest::isBusy() const
{
    return m_state != State::Idle;
}

void PortalScreenshotRequest::onScreenshotCallFinished(
  QDBusPendingCallWatcher* watcher)
{
    if (watcher != m_callWatcher || m_state == State::Idle ||
        m_state == State::Finishing) {
        watcher->deleteLater();
        return;
    }

    QDBusPendingReply<QDBusObjectPath> reply = *watcher;
    m_callWatcher = nullptr;
    watcher->deleteLater();
    CaptureTrace::mark(
      QStringLiteral("portal_method_reply_received"),
      { { QStringLiteral("ok"), !reply.isError() } });

    if (reply.isError()) {
        complete(Result::TransportError,
                 QUrl(),
                 tr("The desktop portal rejected the screenshot request: %1")
                   .arg(reply.error().message()));
        return;
    }

    const QString returnedPath = reply.value().path();
    if (returnedPath.isEmpty() || !returnedPath.startsWith(QLatin1Char('/'))) {
        complete(Result::InvalidResponse,
                 QUrl(),
                 tr("The desktop portal returned an invalid request path"));
        return;
    }

    if (returnedPath != m_predictedPath) {
        // handle_token makes the request path predictable so the Response
        // subscription can exist before Screenshot is sent. Accepting a
        // different path would reintroduce an unavoidable reply/subscription
        // race, so treat a backend that violates that contract as invalid.
        m_requestPath = returnedPath;
        closeRequestAsync();
        complete(Result::InvalidResponse,
                 QUrl(),
                 tr("The desktop portal returned an unexpected request path"));
        return;
    }
    m_requestPath = returnedPath;
    m_state = State::AwaitingResponse;
    if (m_earlyResponse) {
        const auto response = std::move(*m_earlyResponse);
        m_earlyResponse.reset();
        processResponse(response.first, response.second);
    }
}

void PortalScreenshotRequest::onPortalResponse(uint status,
                                               const QVariantMap& results,
                                               const QDBusMessage& message)
{
    // disconnect() cannot retract a D-Bus signal delivery that Qt has already
    // queued. A reused request object must therefore reject a response from a
    // previous request even if its delivery runs after start() has installed
    // the next request's state.
    if (message.path() != m_predictedPath) {
        return;
    }

    CaptureTrace::mark(
      QStringLiteral("portal_response_received"),
      { { QStringLiteral("status"), static_cast<qint64>(status) },
        { QStringLiteral("early"),
          m_state == State::AwaitingMethodReply } });

    if (m_state == State::AwaitingMethodReply) {
        if (!m_earlyResponse) {
            m_earlyResponse = qMakePair(status, results);
        }
        return;
    }
    if (m_state != State::AwaitingResponse) {
        return;
    }
    processResponse(status, results);
}

void PortalScreenshotRequest::processResponse(uint status,
                                              const QVariantMap& results)
{
    if (m_state != State::AwaitingResponse) {
        return;
    }

    if (status == 1) {
        complete(Result::Cancelled);
        return;
    }
    if (status != 0) {
        complete(Result::PortalError,
                 QUrl(),
                 tr("The desktop portal failed the screenshot request (%1)")
                   .arg(status));
        return;
    }

    const QUrl uri(results.value(QStringLiteral("uri")).toString());
    const QString path = uri.toLocalFile();
    if (!uri.isValid() || !uri.isLocalFile() || path.isEmpty() ||
        !QFileInfo(path).isAbsolute()) {
        complete(Result::InvalidResponse,
                 QUrl(),
                 tr("The desktop portal returned an invalid screenshot URI"));
        return;
    }

    complete(Result::Success, uri);
}

void PortalScreenshotRequest::onTimeout()
{
    complete(Result::Timeout,
             QUrl(),
             tr("The desktop portal did not respond before the timeout"));
}

QString PortalScreenshotRequest::makeRequestPath(const QString& token) const
{
    QString sender = m_connection.baseService();
    sender.remove(QLatin1Char(':'));
    sender.replace(QLatin1Char('.'), QLatin1Char('_'));
    return QLatin1String(RequestPathPrefix) + sender + QLatin1Char('/') + token;
}

bool PortalScreenshotRequest::connectResponse(const QString& path)
{
    if (m_connectedPaths.contains(path)) {
        return true;
    }
    if (!m_connection.connect(m_service,
                              path,
                              QLatin1String(RequestInterface),
                              QStringLiteral("Response"),
                              this,
                              SLOT(onPortalResponse(uint,
                                                    QVariantMap,
                                                    QDBusMessage)))) {
        return false;
    }
    m_connectedPaths.append(path);
    return true;
}

void PortalScreenshotRequest::disconnectResponses()
{
    for (const QString& path : std::as_const(m_connectedPaths)) {
        m_connection.disconnect(m_service,
                                path,
                                QLatin1String(RequestInterface),
                                QStringLiteral("Response"),
                                this,
                                SLOT(onPortalResponse(uint,
                                                      QVariantMap,
                                                      QDBusMessage)));
    }
    m_connectedPaths.clear();
}

void PortalScreenshotRequest::closeRequestAsync()
{
    if (!m_connection.isConnected() || m_requestPath.isEmpty() ||
        m_service.isEmpty()) {
        return;
    }
    QDBusMessage close =
      QDBusMessage::createMethodCall(m_service,
                                     m_requestPath,
                                     QLatin1String(RequestInterface),
                                     QStringLiteral("Close"));
    m_connection.asyncCall(close);
    m_requestPath.clear();
}

void PortalScreenshotRequest::complete(Result result,
                                       const QUrl& uri,
                                       const QString& errorMessage)
{
    if (m_state == State::Finishing) {
        return;
    }

    // Validation failures before a call starts are terminal requests too.
    if (m_state == State::Idle && result != Result::TransportError) {
        return;
    }

    m_state = State::Finishing;
    m_timeout.stop();
    disconnectResponses();
    if (m_callWatcher) {
        disconnect(m_callWatcher, nullptr, this, nullptr);
        m_callWatcher->deleteLater();
        m_callWatcher = nullptr;
    }
    if (result == Result::Timeout) {
        closeRequestAsync();
    }
    m_predictedPath.clear();
    m_requestPath.clear();
    m_earlyResponse.reset();
    m_state = State::Idle;
    CaptureTrace::mark(
      QStringLiteral("portal_request_finished"),
      { { QStringLiteral("result"), static_cast<int>(result) } });
    emit finished(result, uri, errorMessage);
}

#endif
