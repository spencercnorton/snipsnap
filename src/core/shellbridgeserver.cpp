// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#include "shellbridgeserver.h"

#include "core/snipsnap.h"
#include "utils/abstractlogger.h"
#include "utils/capturetrace.h"
#include "utils/confighandler.h"
#include "widgets/capture/capturewidget.h"

#include <QBuffer>
#include <QDBusConnection>
#include <QDBusConnectionInterface>
#include <QDBusReply>
#include <QDir>
#include <QFile>
#include <QImageReader>
#include <QLocalServer>
#include <QLocalSocket>
#include <QMetaObject>
#include <QProcess>
#include <QStandardPaths>
#include <QThreadPool>
#include <QTimer>

#include <cerrno>
#include <cstring>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

namespace
{

constexpr int RequestDeadlineMs = 5'000;
constexpr int DecodeDeadlineMs = 3'000;
constexpr int EditorDeadlineMs = 3'000;
constexpr auto SocketDirectoryName = "snipsnap";
constexpr auto SocketFileName = "gnome-shell-bridge-v1.sock";

QByteArray nativePath(const QString& path)
{
    return QFile::encodeName(path);
}

bool statSecureDirectory(const QByteArray& path, uid_t uid)
{
    struct stat info{};
    return ::lstat(path.constData(), &info) == 0 && S_ISDIR(info.st_mode)
      && info.st_uid == uid && (info.st_mode & 0077) == 0;
}

void closeAfterWrite(QLocalSocket* socket)
{
    QObject::connect(socket,
                     &QLocalSocket::disconnected,
                     socket,
                     &QObject::deleteLater);
    socket->flush();
    socket->disconnectFromServer();
}

} // namespace

ShellBridgeServer::ShellBridgeServer(QObject* parent)
  : QObject(parent)
  , m_server(new QLocalServer(this))
  , m_requestTimer(new QTimer(this))
  , m_editorTimer(new QTimer(this))
{
    m_requestTimer->setSingleShot(true);
    m_editorTimer->setSingleShot(true);
    connect(m_server,
            &QLocalServer::newConnection,
            this,
            &ShellBridgeServer::acceptConnections);
    connect(m_requestTimer,
            &QTimer::timeout,
            this,
            &ShellBridgeServer::requestTimedOut);
    connect(m_editorTimer,
            &QTimer::timeout,
            this,
            &ShellBridgeServer::editorTimedOut);
}

ShellBridgeServer::~ShellBridgeServer()
{
    resetConnection(true);
    m_server->close();
}

QString ShellBridgeServer::socketPath()
{
    const QString runtime =
      QStandardPaths::writableLocation(QStandardPaths::RuntimeLocation);
    if (runtime.isEmpty()) {
        return {};
    }
    return runtime + QLatin1Char('/') + QLatin1String(SocketDirectoryName)
      + QLatin1Char('/') + QLatin1String(SocketFileName);
}

bool ShellBridgeServer::start()
{
    if (m_server->isListening()) {
        return true;
    }
    m_errorString.clear();
    m_socketPath = socketPath();
    if (m_socketPath.isEmpty() || !prepareRuntimeDirectory()) {
        return false;
    }

    const QByteArray encoded = nativePath(m_socketPath);
    if (encoded.size() >= static_cast<qsizetype>(sizeof(sockaddr_un::sun_path))) {
        m_errorString = QStringLiteral("GNOME Shell bridge socket path is too long");
        return false;
    }

    m_server->setSocketOptions(QLocalServer::UserAccessOption);
    if (!m_server->listen(m_socketPath)) {
        if (!removeOwnedStaleSocket() || !m_server->listen(m_socketPath)) {
            m_errorString = QStringLiteral("Unable to bind GNOME Shell bridge socket: %1")
                              .arg(m_server->errorString());
            return false;
        }
    }
    if (!validateBoundSocket()) {
        m_server->close();
        return false;
    }
    return true;
}

bool ShellBridgeServer::isListening() const
{
    return m_server->isListening();
}

QString ShellBridgeServer::errorString() const
{
    return m_errorString;
}

bool ShellBridgeServer::prepareRuntimeDirectory()
{
    const uid_t uid = ::geteuid();
    const QString runtime =
      QStandardPaths::writableLocation(QStandardPaths::RuntimeLocation);
    const QByteArray runtimePath = nativePath(runtime);
    if (!statSecureDirectory(runtimePath, uid)) {
        m_errorString =
          QStringLiteral("XDG runtime directory is not an owner-only real directory");
        return false;
    }

    const QString child = runtime + QLatin1Char('/')
      + QLatin1String(SocketDirectoryName);
    const QByteArray childPath = nativePath(child);
    if (::mkdir(childPath.constData(), 0700) != 0 && errno != EEXIST) {
        m_errorString =
          QStringLiteral("Unable to create the private SnipSnap runtime directory");
        return false;
    }
    if (!statSecureDirectory(childPath, uid)) {
        m_errorString = QStringLiteral(
          "Private SnipSnap runtime path is not an owner-only real directory");
        return false;
    }
    return true;
}

bool ShellBridgeServer::removeOwnedStaleSocket()
{
    const QByteArray path = nativePath(m_socketPath);
    struct stat before{};
    if (::lstat(path.constData(), &before) != 0 || !S_ISSOCK(before.st_mode)
        || before.st_uid != ::geteuid()) {
        return false;
    }

    const int descriptor = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (descriptor < 0) {
        return false;
    }
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    std::memcpy(address.sun_path,
                path.constData(),
                static_cast<std::size_t>(path.size() + 1));
    const int result = ::connect(
      descriptor, reinterpret_cast<const sockaddr*>(&address), sizeof(address));
    const int connectError = errno;
    ::close(descriptor);
    if (result == 0 || (connectError != ECONNREFUSED && connectError != ENOENT)) {
        return false;
    }

    struct stat after{};
    if (::lstat(path.constData(), &after) != 0 || !S_ISSOCK(after.st_mode)
        || after.st_uid != ::geteuid() || after.st_dev != before.st_dev
        || after.st_ino != before.st_ino) {
        return false;
    }
    return ::unlink(path.constData()) == 0 || errno == ENOENT;
}

bool ShellBridgeServer::validateBoundSocket()
{
    const QByteArray path = nativePath(m_socketPath);
    if (::chmod(path.constData(), 0600) != 0) {
        m_errorString =
          QStringLiteral("Unable to restrict GNOME Shell bridge socket permissions");
        return false;
    }
    struct stat info{};
    if (::lstat(path.constData(), &info) != 0 || !S_ISSOCK(info.st_mode)
        || info.st_uid != ::geteuid() || (info.st_mode & 0777) != 0600) {
        m_errorString =
          QStringLiteral("GNOME Shell bridge socket failed ownership validation");
        return false;
    }
    return true;
}

bool ShellBridgeServer::validatePeer(QLocalSocket* socket) const
{
    const int descriptor = static_cast<int>(socket->socketDescriptor());
    struct ucred credentials{};
    socklen_t length = sizeof(credentials);
    if (descriptor < 0
        || ::getsockopt(descriptor,
                        SOL_SOCKET,
                        SO_PEERCRED,
                        &credentials,
                        &length)
             != 0
        || length != sizeof(credentials) || credentials.uid != ::geteuid()
        || credentials.pid <= 1) {
        return false;
    }

    QDBusConnectionInterface* bus = QDBusConnection::sessionBus().interface();
    if (bus == nullptr) {
        return false;
    }
    const QDBusReply<uint> shellPid =
      bus->servicePid(QStringLiteral("org.gnome.Shell"));
    return shellPid.isValid()
      && shellPid.value() == static_cast<uint>(credentials.pid);
}

void ShellBridgeServer::acceptConnections()
{
    while (m_server->hasPendingConnections()) {
        QLocalSocket* socket = m_server->nextPendingConnection();
        if (socket == nullptr) {
            continue;
        }
        if (!validatePeer(socket)) {
            socket->abort();
            socket->deleteLater();
            continue;
        }
        if (m_socket || m_phase != Phase::Idle || m_decodeInFlight
            || m_editor) {
            const QByteArray busy = ShellBridgeProtocol::encodeAck(
              { 0,
                ShellBridgeProtocol::AckStatus::Busy,
                static_cast<quint32>(::getpid()) });
            if (socket->write(busy) != busy.size()) {
                socket->abort();
                socket->deleteLater();
            } else {
                closeAfterWrite(socket);
            }
            continue;
        }

        ++m_generation;
        m_socket = socket;
        m_phase = Phase::Receiving;
        m_parser.reset();
        m_request = {};
        m_commitBuffer.clear();
        m_traceStart = CaptureTrace::captureRequestStart();
        connect(socket,
                &QLocalSocket::readyRead,
                this,
                &ShellBridgeServer::readRequest);
        connect(socket,
                &QLocalSocket::disconnected,
                this,
                &ShellBridgeServer::peerDisconnected);
        m_requestTimer->start(RequestDeadlineMs);
        if (socket->bytesAvailable() > 0) {
            readRequest();
        }
    }
}

void ShellBridgeServer::readRequest()
{
    if (!m_socket) {
        return;
    }
    if (m_phase == Phase::AwaitingClientCommit) {
        m_commitBuffer.append(m_socket->readAll());
        if (m_commitBuffer.size() > ShellBridgeProtocol::CommitSize) {
            fail(ShellBridgeProtocol::AckStatus::MalformedRequest);
            return;
        }
        if (m_commitBuffer.size() == ShellBridgeProtocol::CommitSize) {
            quint64 captureId = 0;
            if (!ShellBridgeProtocol::decodeCommit(m_commitBuffer, &captureId)
                || captureId != m_request.captureId) {
                fail(ShellBridgeProtocol::AckStatus::MalformedRequest);
                return;
            }
            m_editorTimer->stop();
            m_phase = Phase::Committed;
            if (m_traceOwned) {
                CaptureTrace::mark(QStringLiteral("editor_committed"));
            }
            if (!m_annotatorPng.isEmpty()) {
                // Spawn before the ACK: the commit is already irrevocable on
                // both sides, and a failed ACK write must not lose the
                // capture.
                spawnAnnotator(m_annotatorProgram, m_annotatorPng);
                m_annotatorPng.clear();
                m_annotatorProgram.clear();
                if (m_traceOwned) {
                    CaptureTrace::finish(
                      QStringLiteral("capture_completed"),
                      { { QStringLiteral("annotator"),
                          QStringLiteral("external") } });
                    m_traceOwned = false;
                }
            }
            if (!sendAck(ShellBridgeProtocol::AckStatus::CommitAccepted,
                         captureId)) {
                resetConnection(false);
                return;
            }
            resetConnection(false);
        }
        return;
    }
    if (m_phase != Phase::Receiving) {
        if (m_socket->bytesAvailable() > 0) {
            fail(ShellBridgeProtocol::AckStatus::MalformedRequest);
        }
        return;
    }

    const ShellBridgeProtocol::ParseStatus status =
      m_parser.append(m_socket->readAll());
    if (status == ShellBridgeProtocol::ParseStatus::Error) {
        fail(ShellBridgeProtocol::AckStatus::MalformedRequest);
        return;
    }
    if (status == ShellBridgeProtocol::ParseStatus::Complete) {
        m_requestTimer->stop();
        m_request = m_parser.request();
        m_traceOwned = CaptureTrace::begin(
          QStringLiteral("gnome_shell_bridge"), m_traceStart);
        if (m_traceOwned) {
            CaptureTrace::mark(
              QStringLiteral("shell_bridge_region_received"),
              { { QStringLiteral("bridge_capture_id"),
                  QString::number(m_request.captureId) },
                { QStringLiteral("encoded_bytes"),
                  static_cast<qint64>(m_request.payloadLength) },
                { QStringLiteral("pixel_width"),
                  static_cast<int>(m_request.pixelWidth) },
                { QStringLiteral("pixel_height"),
                  static_cast<int>(m_request.pixelHeight) } });
        }
        if (!sendAck(ShellBridgeProtocol::AckStatus::RequestAccepted,
                     m_request.captureId)) {
            resetConnection(true);
            return;
        }
        m_phase = Phase::Decoding;
        m_requestTimer->start(DecodeDeadlineMs);
        beginDecode();
    }
}

void ShellBridgeServer::beginDecode()
{
    const quint64 generation = m_generation;
    m_decodeInFlight = true;
    ShellBridgeProtocol::Request request = m_request;
    // External-annotator mode is decided per capture, here, so the daemon and
    // its ACK behavior stay consistent even if config or PATH change mid-run.
    // The stash is a COW share with the worker's copy — no allocation.
    m_annotatorProgram = annotatorProgram();
    m_annotatorPng =
      m_annotatorProgram.isEmpty() ? QByteArray() : m_request.png;
    m_request.png.clear();
    m_parser.reset();
    QPointer<ShellBridgeServer> self(this);
    QThreadPool::globalInstance()->start(
      [self, generation, request = std::move(request)]() mutable {
          QImage image;
          QBuffer buffer(&request.png);
          if (buffer.open(QIODevice::ReadOnly)) {
              QImageReader reader(&buffer, "PNG");
              reader.setAutoTransform(false);
              if (reader.size()
                  == QSize(static_cast<int>(request.pixelWidth),
                           static_cast<int>(request.pixelHeight))) {
                  image = reader.read();
                  if (image.size()
                      != QSize(static_cast<int>(request.pixelWidth),
                               static_cast<int>(request.pixelHeight))) {
                      image = {};
                  }
              }
          }
          if (!self) {
              return;
          }
          QMetaObject::invokeMethod(
            self.data(),
            [self, generation, image = std::move(image)]() {
                if (self) {
                    self->decoded(generation, image);
                }
            },
            Qt::QueuedConnection);
      });
}

void ShellBridgeServer::decoded(quint64 generation, const QImage& image)
{
    m_decodeInFlight = false;
    if (generation != m_generation || m_phase != Phase::Decoding) {
        if (!m_socket && !m_editor) {
            m_phase = Phase::Idle;
        }
        return;
    }
    m_requestTimer->stop();
    if (image.isNull()) {
        fail(ShellBridgeProtocol::AckStatus::DecodeFailed);
        return;
    }

    if (!m_annotatorPng.isEmpty()) {
        // External annotator: no in-process editor. EDITOR_READY here means
        // "the capture decoded and the daemon is ready to hand it off"; the
        // extension in external-annotator mode commits without looking for a
        // daemon-owned window, and the commit gates the spawn.
        m_phase = Phase::AwaitingClientCommit;
        if (m_traceOwned) {
            CaptureTrace::mark(QStringLiteral("editor_ready"),
                               { { QStringLiteral("annotator"),
                                   QStringLiteral("external") } });
        }
        if (!sendAck(ShellBridgeProtocol::AckStatus::EditorReady,
                     m_request.captureId)) {
            resetConnection(true);
            return;
        }
        m_editorTimer->start(EditorDeadlineMs);
        return;
    }

    const QRect sourceRect(m_request.logicalX,
                           m_request.logicalY,
                           static_cast<int>(m_request.logicalWidth),
                           static_cast<int>(m_request.logicalHeight));
    const qreal scale = static_cast<qreal>(m_request.scaleNumerator)
      / static_cast<qreal>(m_request.scaleDenominator);
    CaptureWidget* editor = SnipSnap::instance()->openShellBridgeRegion(
      image, sourceRect, scale, m_request.captureId);
    if (editor == nullptr) {
        fail(ShellBridgeProtocol::AckStatus::InternalError);
        return;
    }

    m_editor = editor;
    m_phase = Phase::AwaitingEditorPaint;
    const quint64 captureId = m_request.captureId;
    connect(editor,
            &CaptureWidget::firstPaintCompleted,
            this,
            [this, generation, captureId]() {
                if (generation != m_generation
                    || m_phase != Phase::AwaitingEditorPaint) {
                    return;
                }
                m_editorTimer->stop();
                m_phase = Phase::AwaitingClientCommit;
                if (m_traceOwned) {
                    CaptureTrace::mark(QStringLiteral("editor_ready"));
                }
                if (!sendAck(ShellBridgeProtocol::AckStatus::EditorReady,
                             captureId)) {
                    resetConnection(true);
                    return;
                }
                m_editorTimer->start(EditorDeadlineMs);
            });
    connect(editor, &QObject::destroyed, this, [this, generation]() {
        if (generation == m_generation
            && (m_phase == Phase::AwaitingEditorPaint
                || m_phase == Phase::AwaitingClientCommit)) {
            m_editor = nullptr;
            fail(ShellBridgeProtocol::AckStatus::InternalError);
        }
    });
    m_editorTimer->start(EditorDeadlineMs);
}

bool ShellBridgeServer::sendAck(ShellBridgeProtocol::AckStatus status,
                                quint64 captureId)
{
    if (!m_socket) {
        return false;
    }
    const QByteArray bytes = ShellBridgeProtocol::encodeAck(
      { captureId, status, static_cast<quint32>(::getpid()) });
    return m_socket->write(bytes) == bytes.size() && m_socket->flush();
}

void ShellBridgeServer::fail(ShellBridgeProtocol::AckStatus status)
{
    quint64 captureId = m_request.captureId;
    if (captureId == 0) {
        captureId = m_parser.request().captureId;
    }
    (void)sendAck(status, captureId);
    if (m_traceOwned && !m_editor) {
        CaptureTrace::finish(
          QStringLiteral("capture_failed"),
          { { QStringLiteral("reason"),
              QStringLiteral("shell_bridge_handoff") },
            { QStringLiteral("status"),
              static_cast<qint64>(status) } });
    }
    m_traceOwned = false;
    resetConnection(true);
}

void ShellBridgeServer::peerDisconnected()
{
    if (sender() != m_socket) {
        return;
    }
    if (m_traceOwned && !m_editor) {
        CaptureTrace::finish(
          QStringLiteral("capture_cancelled"),
          { { QStringLiteral("reason"),
              QStringLiteral("shell_bridge_peer_disconnected") } });
    }
    m_traceOwned = false;
    resetConnection(m_phase != Phase::Committed);
}

void ShellBridgeServer::requestTimedOut()
{
    fail(ShellBridgeProtocol::AckStatus::Timeout);
}

void ShellBridgeServer::editorTimedOut()
{
    fail(ShellBridgeProtocol::AckStatus::Timeout);
}

void ShellBridgeServer::resetConnection(bool closeUncommittedEditor)
{
    ++m_generation;
    m_requestTimer->stop();
    m_editorTimer->stop();
    if (m_traceOwned && !m_editor) {
        CaptureTrace::finish(
          QStringLiteral("capture_failed"),
          { { QStringLiteral("reason"),
              QStringLiteral("shell_bridge_reset") } });
    }
    m_parser.reset();
    m_request = {};
    m_commitBuffer.clear();
    m_annotatorPng.clear();
    m_annotatorProgram.clear();
    m_traceOwned = false;
    m_traceStart = {};
    // QImageReader cannot be cancelled once dispatched. Keep the one-slot
    // receiver busy until that worker actually returns, even after its peer
    // has timed out, so retries cannot accumulate decoded buffers or CPU work.
    m_phase = m_decodeInFlight ? Phase::Decoding : Phase::Idle;

    if (closeUncommittedEditor) {
        QPointer<CaptureWidget> editor = m_editor;
        m_editor = nullptr;
        if (editor) {
            editor->close();
        }
    }

    QPointer<QLocalSocket> socket = m_socket;
    m_socket = nullptr;
    if (socket) {
        disconnect(socket, nullptr, this, nullptr);
        if (socket->state() == QLocalSocket::UnconnectedState) {
            socket->deleteLater();
        } else {
            closeAfterWrite(socket);
        }
    }
}

QString ShellBridgeServer::annotatorProgram()
{
    // Test/power-user hook: an explicit program path wins outright.
    const QString envOverride =
      qEnvironmentVariable("SNIPSNAP_BRIDGE_ANNOTATOR");
    if (!envOverride.isEmpty()) {
        return envOverride;
    }
    // Off by default; the bridge activation manager enables this together
    // with the extension's external-annotator gschema key so both halves of
    // the handshake agree on the mode.
    if (!ConfigHandler().bridgeUseExternalAnnotator()) {
        return {};
    }
    return QStandardPaths::findExecutable(QStringLiteral("satty"));
}

void ShellBridgeServer::spawnAnnotator(const QString& program,
                                       const QByteArray& png)
{
    if (program.isEmpty()) {
        AbstractLogger::error()
          << QStringLiteral("Shell bridge annotator vanished before spawn");
        return;
    }
    QString directory = ConfigHandler().savePath();
    if (directory.isEmpty()) {
        directory =
          QStandardPaths::writableLocation(QStandardPaths::PicturesLocation);
    }
    // Deliberately parentless: ~QProcess kills its child, and an annotator
    // window must survive a daemon restart or upgrade mid-annotation. The
    // object cleans up via the finished/errorOccurred signals; if the daemon
    // exits first, the annotator is orphaned to init and keeps running.
    auto* process = new QProcess();
    process->setProgram(program);
    // The annotator's own output is the only diagnostic when a spawn dies
    // (2026-07-23: a silent failure cost a debugging round-trip because the
    // default QProcess pipes discarded it). One merged log, truncated per
    // spawn, in the user cache dir.
    process->setProcessChannelMode(QProcess::MergedChannels);
    const QString logDirectory =
      QStandardPaths::writableLocation(QStandardPaths::CacheLocation);
    if (!logDirectory.isEmpty() && QDir().mkpath(logDirectory)) {
        process->setStandardOutputFile(logDirectory
                                       + QStringLiteral("/annotator.log"));
    }
    // satty expands the strftime pattern itself; the PNG arrives on stdin so
    // nothing touches disk unless the user saves.
    // --title/--app-id need satty >= 0.21.0 (Ubuntu 26.04 ships 0.21.1). They carry
    // this product's name and identity onto the annotator window, so the editor
    // the user sees after a capture is SnipSnap rather than a third-party app.
    process->setArguments(
      { QStringLiteral("--filename"),
        QStringLiteral("-"),
        QStringLiteral("--output-filename"),
        directory + QStringLiteral("/snipsnap-%Y-%m-%d_%H-%M-%S.png"),
        QStringLiteral("--early-exit"),
        QStringLiteral("--copy-command"),
        QStringLiteral("wl-copy"),
        QStringLiteral("--title"),
        QStringLiteral("SnipSnap"),
        QStringLiteral("--app-id"),
        QStringLiteral("tech.norvi.snipsnap") });
    connect(process, &QProcess::started, process, [process, png]() {
        process->write(png);
        process->closeWriteChannel();
    });
    connect(process, &QProcess::finished, process, &QObject::deleteLater);
    connect(process,
            &QProcess::errorOccurred,
            process,
            [process](QProcess::ProcessError error) {
                AbstractLogger::error()
                  << QStringLiteral("Shell bridge annotator failed (%1): %2")
                       .arg(process->program())
                       .arg(static_cast<int>(error));
                process->deleteLater();
            });
    process->start();
}
