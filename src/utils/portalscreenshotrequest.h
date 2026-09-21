// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#pragma once

#include <QtGlobal>

#if defined(Q_OS_UNIX) && !defined(Q_OS_MACOS)

#include <QDBusConnection>
#include <QObject>
#include <QStringList>
#include <QTimer>
#include <QUrl>
#include <optional>

class QDBusPendingCallWatcher;
class QDBusMessage;

class PortalScreenshotRequest : public QObject
{
    Q_OBJECT

public:
    enum class Result
    {
        Success,
        Cancelled,
        PortalError,
        TransportError,
        Timeout,
        InvalidResponse
    };
    Q_ENUM(Result)

    explicit PortalScreenshotRequest(QObject* parent = nullptr);
    PortalScreenshotRequest(const QDBusConnection& connection,
                            QString service,
                            QObject* parent = nullptr);
    ~PortalScreenshotRequest() override;

    bool start(const QString& parentWindow,
               int timeoutMs = 300000,
               bool interactive = false);
    bool isBusy() const;

signals:
    void finished(PortalScreenshotRequest::Result result,
                  const QUrl& uri,
                  const QString& errorMessage);
    void busyRejected();

private slots:
    void onScreenshotCallFinished(QDBusPendingCallWatcher* watcher);
    void onPortalResponse(uint status,
                          const QVariantMap& results,
                          const QDBusMessage& message);
    void onTimeout();

private:
    enum class State
    {
        Idle,
        AwaitingMethodReply,
        AwaitingResponse,
        Finishing
    };

    QString makeRequestPath(const QString& token) const;
    bool connectResponse(const QString& path);
    void processResponse(uint status, const QVariantMap& results);
    void disconnectResponses();
    void closeRequestAsync();
    void complete(Result result,
                  const QUrl& uri = QUrl(),
                  const QString& errorMessage = QString());

    QDBusConnection m_connection;
    QString m_service;
    QString m_predictedPath;
    QString m_requestPath;
    QStringList m_connectedPaths;
    QDBusPendingCallWatcher* m_callWatcher{ nullptr };
    std::optional<QPair<uint, QVariantMap>> m_earlyResponse;
    QTimer m_timeout;
    State m_state{ State::Idle };
};

Q_DECLARE_METATYPE(PortalScreenshotRequest::Result)

#endif
