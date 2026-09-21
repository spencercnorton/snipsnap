// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#include "utils/portalscreenshotrequest.h"

#include <QDBusConnection>
#include <QDBusContext>
#include <QDBusError>
#include <QDBusMessage>
#include <QDBusObjectPath>
#include <QFileInfo>
#include <QImage>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QTest>
#include <QTimer>
#include <QUrl>
#include <utility>

namespace {

constexpr auto PortalService = "org.freedesktop.portal.Desktop";
constexpr auto PortalPath = "/org/freedesktop/portal/desktop";

class FakeRequest : public QObject
{
    Q_OBJECT
    Q_CLASSINFO("D-Bus Interface", "org.freedesktop.portal.Request")

public:
    explicit FakeRequest(QObject* parent = nullptr)
      : QObject(parent)
    {}

    int closeCount{ 0 };

public slots:
    void Close() { ++closeCount; }

signals:
    void Response(uint status, const QVariantMap& results);
};

class FakePortal
  : public QObject
  , protected QDBusContext
{
    Q_OBJECT
    Q_CLASSINFO("D-Bus Interface", "org.freedesktop.portal.Screenshot")

public:
    enum class Behavior
    {
        Success,
        Cancel,
        Failure,
        ImmediateError,
        MismatchedPath,
        NoResponse,
        MethodNoReply,
        DuplicateResponse,
        EarlyResponse,
        MissingUri,
        InvalidUri,
        NonLocalUri
    };

    explicit FakePortal(QDBusConnection connection, QObject* parent = nullptr)
      : QObject(parent)
      , m_connection(std::move(connection))
    {}

    void setBehavior(Behavior behavior) { m_behavior = behavior; }
    void setImagePath(QString path) { m_imagePath = std::move(path); }
    FakeRequest* lastRequest() const { return m_lastRequest; }
    QVariantMap lastOptions() const { return m_lastOptions; }

public slots:
    QDBusObjectPath Screenshot(const QString&, const QVariantMap& options)
    {
        m_lastOptions = options;
        if (m_behavior == Behavior::ImmediateError) {
            sendErrorReply(QDBusError::Failed,
                           QStringLiteral("fake immediate failure"));
            return QDBusObjectPath(QStringLiteral("/"));
        }

        QString sender = message().service();
        sender.remove(QLatin1Char(':'));
        sender.replace(QLatin1Char('.'), QLatin1Char('_'));
        const QString token =
          options.value(QStringLiteral("handle_token")).toString();
        QString path =
          QStringLiteral("/org/freedesktop/portal/desktop/request/") + sender +
          QLatin1Char('/') + token;
        if (m_behavior == Behavior::MismatchedPath) {
            path += QStringLiteral("_returned");
        }

        auto* request = new FakeRequest(this);
        m_lastRequest = request;
        const bool registered = m_connection.registerObject(
          path,
          request,
          QDBusConnection::ExportAllSlots | QDBusConnection::ExportAllSignals);
        if (!registered) {
            sendErrorReply(QDBusError::Failed,
                           QStringLiteral("fake request registration failed"));
            request->deleteLater();
            return QDBusObjectPath(QStringLiteral("/"));
        }

        if (m_behavior == Behavior::MethodNoReply) {
            setDelayedReply(true);
            return QDBusObjectPath(path);
        }

        const auto sendResponse = [this, request]() {
            QVariantMap results;
            uint status = 0;
            if (m_behavior == Behavior::Cancel) {
                status = 1;
            } else if (m_behavior == Behavior::Failure) {
                status = 2;
            } else if (m_behavior == Behavior::MissingUri) {
                // Successful status with no URI is an invalid response.
            } else if (m_behavior == Behavior::InvalidUri) {
                results.insert(QStringLiteral("uri"),
                               QStringLiteral("not a local URI"));
            } else if (m_behavior == Behavior::NonLocalUri) {
                results.insert(QStringLiteral("uri"),
                               QStringLiteral("https://example.invalid/a.png"));
            } else {
                results.insert(QStringLiteral("uri"),
                               QUrl::fromLocalFile(m_imagePath).toString());
            }
            emit request->Response(status, results);
            if (m_behavior == Behavior::DuplicateResponse) {
                emit request->Response(status, results);
                QTimer::singleShot(30, request, [request, status, results]() {
                    emit request->Response(status, results);
                });
            }
        };

        if (m_behavior != Behavior::NoResponse) {
            if (m_behavior == Behavior::EarlyResponse) {
                sendResponse();
            } else {
                QTimer::singleShot(20, request, sendResponse);
            }
        }

        return QDBusObjectPath(path);
    }

private:
    QDBusConnection m_connection;
    Behavior m_behavior{ Behavior::Success };
    QString m_imagePath;
    FakeRequest* m_lastRequest{ nullptr };
    QVariantMap m_lastOptions;
};

} // namespace

class PortalScreenshotRequestTest : public QObject
{
    Q_OBJECT

private slots:
    void initTestCase()
    {
        QVERIFY2(m_connection.isConnected(),
                 "test must run inside a private dbus-run-session bus");
        QVERIFY(m_serviceConnection.isConnected());
        QVERIFY(
          m_serviceConnection.registerService(QLatin1String(PortalService)));
        QVERIFY(
          m_serviceConnection.registerObject(QLatin1String(PortalPath),
                                             &m_portal,
                                             QDBusConnection::ExportAllSlots));

        QVERIFY(m_tempDir.isValid());
        m_imagePath = m_tempDir.filePath(QStringLiteral("portal.png"));
        QImage image(4, 3, QImage::Format_ARGB32_Premultiplied);
        image.fill(Qt::green);
        QVERIFY(image.save(m_imagePath));
        m_portal.setImagePath(m_imagePath);
    }

    void cleanupTestCase()
    {
        m_serviceConnection.unregisterObject(QLatin1String(PortalPath));
        m_serviceConnection.unregisterService(QLatin1String(PortalService));
        QDBusConnection::disconnectFromBus(
          QStringLiteral("fake-portal-service"));
    }

    void cleanup()
    {
        if (!QFileInfo::exists(m_imagePath)) {
            QImage image(4, 3, QImage::Format_ARGB32_Premultiplied);
            image.fill(Qt::green);
            QVERIFY(image.save(m_imagePath));
        }
        m_portal.setImagePath(m_imagePath);
        QTest::qWait(10);
    }

    void success()
    {
        m_portal.setBehavior(FakePortal::Behavior::Success);
        PortalScreenshotRequest request(m_connection,
                                        QLatin1String(PortalService));
        QSignalSpy finished(&request, &PortalScreenshotRequest::finished);

        QVERIFY(request.start(QString(), 500));
        QTRY_COMPARE(finished.count(), 1);
        QCOMPARE(m_portal.lastOptions().value(QStringLiteral("interactive")),
                 QVariant(false));
        QCOMPARE(resultAt(finished), PortalScreenshotRequest::Result::Success);
        const QUrl uri = qvariant_cast<QUrl>(finished.at(0).at(1));
        QCOMPARE(uri.toLocalFile(), m_imagePath);
        QVERIFY(QFileInfo::exists(m_imagePath));
        QTest::qWait(20);
        QCOMPARE(m_portal.lastRequest()->closeCount, 0);
    }

    void userCancel()
    {
        expect(FakePortal::Behavior::Cancel,
               PortalScreenshotRequest::Result::Cancelled);
    }

    void portalStatusFailure()
    {
        expect(FakePortal::Behavior::Failure,
               PortalScreenshotRequest::Result::PortalError);
    }

    void immediateDbusError()
    {
        expect(FakePortal::Behavior::ImmediateError,
               PortalScreenshotRequest::Result::TransportError);
    }

    void mismatchedReturnedRequestPath()
    {
        m_portal.setBehavior(FakePortal::Behavior::MismatchedPath);
        PortalScreenshotRequest request(m_connection,
                                        QLatin1String(PortalService));
        QSignalSpy finished(&request, &PortalScreenshotRequest::finished);

        QVERIFY(request.start(QString(), 500));
        QTRY_COMPARE(finished.count(), 1);
        QCOMPARE(resultAt(finished),
                 PortalScreenshotRequest::Result::InvalidResponse);
        QTRY_VERIFY(m_portal.lastRequest()->closeCount > 0);
        QTest::qWait(50);
        QCOMPARE(finished.count(), 1);
    }

    void timeoutAndLateResponseAreExactOnce()
    {
        m_portal.setBehavior(FakePortal::Behavior::NoResponse);
        PortalScreenshotRequest request(m_connection,
                                        QLatin1String(PortalService));
        QSignalSpy finished(&request, &PortalScreenshotRequest::finished);

        QVERIFY(request.start(QString(), 30));
        QTRY_COMPARE(finished.count(), 1);
        QCOMPARE(resultAt(finished), PortalScreenshotRequest::Result::Timeout);
        FakeRequest* fakeRequest = m_portal.lastRequest();
        QVERIFY(fakeRequest);
        emit fakeRequest->Response(
          0,
          { { QStringLiteral("uri"),
              QUrl::fromLocalFile(m_imagePath).toString() } });
        QTest::qWait(50);
        QCOMPARE(finished.count(), 1);
        QTRY_VERIFY(fakeRequest->closeCount > 0);
    }

    void methodReplyTimeout()
    {
        m_portal.setBehavior(FakePortal::Behavior::MethodNoReply);
        PortalScreenshotRequest request(m_connection,
                                        QLatin1String(PortalService));
        QSignalSpy finished(&request, &PortalScreenshotRequest::finished);
        QVERIFY(request.start(QString(), 30));
        QTRY_COMPARE(finished.count(), 1);
        QCOMPARE(resultAt(finished), PortalScreenshotRequest::Result::Timeout);
        QTRY_VERIFY(m_portal.lastRequest()->closeCount > 0);
    }

    void earlyResponseIsBufferedUntilMethodReply()
    {
        expect(FakePortal::Behavior::EarlyResponse,
               PortalScreenshotRequest::Result::Success);
    }

    void invalidUri()
    {
        expect(FakePortal::Behavior::InvalidUri,
               PortalScreenshotRequest::Result::InvalidResponse);
    }

    void missingUri()
    {
        expect(FakePortal::Behavior::MissingUri,
               PortalScreenshotRequest::Result::InvalidResponse);
    }

    void nonLocalUri()
    {
        expect(FakePortal::Behavior::NonLocalUri,
               PortalScreenshotRequest::Result::InvalidResponse);
    }

    void duplicateResponseIsExactOnce()
    {
        m_portal.setBehavior(FakePortal::Behavior::DuplicateResponse);
        PortalScreenshotRequest request(m_connection,
                                        QLatin1String(PortalService));
        QSignalSpy finished(&request, &PortalScreenshotRequest::finished);

        QVERIFY(request.start(QString(), 500));
        QTRY_COMPARE(finished.count(), 1);
        QTest::qWait(100);
        QCOMPARE(finished.count(), 1);
        QCOMPARE(resultAt(finished), PortalScreenshotRequest::Result::Success);
    }

    void staleQueuedResponseCannotCompleteReusedRequest()
    {
        m_portal.setBehavior(FakePortal::Behavior::DuplicateResponse);
        PortalScreenshotRequest request(m_connection,
                                        QLatin1String(PortalService));
        int completionCount = 0;
        connect(&request,
                &PortalScreenshotRequest::finished,
                &request,
                [&](PortalScreenshotRequest::Result,
                    const QUrl&,
                    const QString&) {
                    ++completionCount;
                    if (completionCount == 1) {
                        m_portal.setBehavior(FakePortal::Behavior::NoResponse);
                        QVERIFY(request.start(QString(), 80));
                    }
                });
        QSignalSpy finished(&request, &PortalScreenshotRequest::finished);

        QVERIFY(request.start(QString(), 500));
        QTRY_COMPARE(finished.count(), 2);
        QCOMPARE(resultAt(finished, 0),
                 PortalScreenshotRequest::Result::Success);
        QCOMPARE(resultAt(finished, 1),
                 PortalScreenshotRequest::Result::Timeout);
        QTest::qWait(50);
        QCOMPARE(finished.count(), 2);
    }

    void invalidService()
    {
        PortalScreenshotRequest request(
          m_connection, QStringLiteral("org.freedesktop.portal.Missing"));
        QSignalSpy finished(&request, &PortalScreenshotRequest::finished);

        QVERIFY(request.start(QString(), 500));
        QTRY_COMPARE(finished.count(), 1);
        QCOMPARE(resultAt(finished),
                 PortalScreenshotRequest::Result::TransportError);
    }

    void invalidBus()
    {
        QDBusConnection invalid(QStringLiteral("missing-test-connection"));
        PortalScreenshotRequest request(invalid, QLatin1String(PortalService));
        QSignalSpy finished(&request, &PortalScreenshotRequest::finished);

        QVERIFY(request.start(QString(), 500));
        QCOMPARE(finished.count(), 1);
        QCOMPARE(resultAt(finished),
                 PortalScreenshotRequest::Result::TransportError);
    }

    void concurrentRequestRejected()
    {
        m_portal.setBehavior(FakePortal::Behavior::NoResponse);
        PortalScreenshotRequest request(m_connection,
                                        QLatin1String(PortalService));
        QSignalSpy finished(&request, &PortalScreenshotRequest::finished);
        QSignalSpy rejected(&request, &PortalScreenshotRequest::busyRejected);

        QVERIFY(request.start(QString(), 40));
        QVERIFY(!request.start(QString(), 40));
        QCOMPARE(rejected.count(), 1);
        QTRY_COMPARE(finished.count(), 1);
        QCOMPARE(resultAt(finished), PortalScreenshotRequest::Result::Timeout);
    }

    void destructionClosesOutstandingRequest()
    {
        m_portal.setBehavior(FakePortal::Behavior::NoResponse);
        auto* request = new PortalScreenshotRequest(
          m_connection, QLatin1String(PortalService));
        QVERIFY(request->start(QString(), 500));
        QTRY_VERIFY(m_portal.lastRequest());
        FakeRequest* fakeRequest = m_portal.lastRequest();

        delete request;
        QTRY_VERIFY(fakeRequest->closeCount > 0);
    }

private:
    void expect(FakePortal::Behavior behavior,
                PortalScreenshotRequest::Result expected)
    {
        m_portal.setBehavior(behavior);
        PortalScreenshotRequest request(m_connection,
                                        QLatin1String(PortalService));
        QSignalSpy finished(&request, &PortalScreenshotRequest::finished);
        QVERIFY(request.start(QString(), 500));
        QTRY_COMPARE(finished.count(), 1);
        QCOMPARE(resultAt(finished), expected);
    }

    static PortalScreenshotRequest::Result resultAt(const QSignalSpy& spy,
                                                     int index = 0)
    {
        return qvariant_cast<PortalScreenshotRequest::Result>(
          spy.at(index).at(0));
    }

    QDBusConnection m_connection{ QDBusConnection::sessionBus() };
    QDBusConnection m_serviceConnection{ QDBusConnection::connectToBus(
      QDBusConnection::SessionBus,
      QStringLiteral("fake-portal-service")) };
    FakePortal m_portal{ m_serviceConnection };
    QTemporaryDir m_tempDir;
    QString m_imagePath;
};

QTEST_MAIN(PortalScreenshotRequestTest)

#include "portal_screenshot_request_test.moc"
