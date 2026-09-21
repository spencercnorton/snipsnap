// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#pragma once

#include "core/shellbridgeprotocol.h"
#include "utils/capturetrace.h"

#include <QObject>
#include <QPointer>
#include <QString>

class CaptureWidget;
class QImage;
class QLocalServer;
class QLocalSocket;
class QTimer;

class ShellBridgeServer final : public QObject
{
    Q_OBJECT

public:
    explicit ShellBridgeServer(QObject* parent = nullptr);
    ~ShellBridgeServer() override;

    bool start();
    bool isListening() const;
    QString errorString() const;
    static QString socketPath();

private slots:
    void acceptConnections();
    void readRequest();
    void peerDisconnected();
    void requestTimedOut();
    void editorTimedOut();

private:
    enum class Phase
    {
        Idle,
        Receiving,
        Decoding,
        AwaitingEditorPaint,
        AwaitingClientCommit,
        Committed,
    };

    bool prepareRuntimeDirectory();
    bool removeOwnedStaleSocket();
    bool validateBoundSocket();
    bool validatePeer(QLocalSocket* socket) const;
    void beginDecode();
    void decoded(quint64 generation, const QImage& image);
    bool sendAck(ShellBridgeProtocol::AckStatus status, quint64 captureId);
    void fail(ShellBridgeProtocol::AckStatus status);
    void resetConnection(bool closeUncommittedEditor);
    static QString annotatorProgram();
    void spawnAnnotator(const QString& program, const QByteArray& png);

    QLocalServer* m_server;
    QPointer<QLocalSocket> m_socket;
    QPointer<CaptureWidget> m_editor;
    QTimer* m_requestTimer;
    QTimer* m_editorTimer;
    ShellBridgeProtocol::RequestParser m_parser;
    ShellBridgeProtocol::Request m_request;
    QByteArray m_commitBuffer;
    // Non-empty only while an external-annotator capture awaits its commit;
    // the commit gates the spawn so a retried handoff cannot open two
    // annotator windows. The program is resolved once, at request receipt,
    // so a config or PATH change mid-capture cannot drop an ACKed capture.
    QByteArray m_annotatorPng;
    QString m_annotatorProgram;
    QString m_socketPath;
    QString m_errorString;
    Phase m_phase{ Phase::Idle };
    quint64 m_generation{ 0 };
    bool m_decodeInFlight{ false };
    bool m_traceOwned{ false };
    CaptureTrace::TimelineStart m_traceStart;
};
