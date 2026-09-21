// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#include "core/shellbridgeprotocol.h"
#include "utils/capturetrace.h"

#include <QBuffer>
#include <QColor>
#include <QCoreApplication>
#include <QDBusConnection>
#include <QDir>
#include <QElapsedTimer>
#include <QFile>
#include <QImage>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLocalSocket>
#include <QProcess>
#include <QProcessEnvironment>
#include <QTemporaryDir>
#include <QTest>
#include <QtEndian>

#include <cmath>
#include <sys/stat.h>

namespace
{

constexpr auto ShellService = "org.gnome.Shell";

QByteArray readAck(QLocalSocket& socket)
{
    QByteArray result;
    QElapsedTimer deadline;
    deadline.start();
    while (result.size() < ShellBridgeProtocol::AckSize
           && deadline.elapsed() < 4'000) {
        if (socket.bytesAvailable() == 0) {
            socket.waitForReadyRead(100);
        }
        result.append(socket.read(ShellBridgeProtocol::AckSize - result.size()));
    }
    return result;
}

void writeU32(QByteArray& bytes, qsizetype offset, quint32 value)
{
    qToBigEndian<quint32>(
      value, reinterpret_cast<uchar*>(bytes.data() + offset));
}

void writeU64(QByteArray& bytes, qsizetype offset, quint64 value)
{
    qToBigEndian<quint64>(
      value, reinterpret_cast<uchar*>(bytes.data() + offset));
}

QImage landmarkImage(const QSize& size)
{
    QImage image(size, QImage::Format_ARGB32_Premultiplied);
    for (int y = 0; y < image.height(); ++y) {
        auto* scanLine = reinterpret_cast<QRgb*>(image.scanLine(y));
        for (int x = 0; x < image.width(); ++x) {
            scanLine[x] = qRgba((x * 37 + y * 13) % 251,
                                (x * 11 + y * 43) % 251,
                                (x * 29 + y * 17) % 251,
                                255);
        }
    }

    image.setPixelColor(0, 0, QColor(240, 10, 20));
    image.setPixelColor(image.width() - 1, 0, QColor(20, 230, 30));
    image.setPixelColor(0, image.height() - 1, QColor(30, 40, 220));
    image.setPixelColor(
      image.width() - 1, image.height() - 1, QColor(230, 220, 40));
    image.setPixelColor(1, 1, QColor(20, 210, 220));
    return image;
}

QByteArray validRequest(quint64 captureId,
                        qint32 logicalX = -40,
                        qint32 logicalY = -24,
                        quint32 logicalWidth = 100,
                        quint32 logicalHeight = 80,
                        quint32 pixelWidth = 125,
                        quint32 pixelHeight = 100,
                        QImage image = {})
{
    const QSize pixelSize(static_cast<int>(pixelWidth),
                          static_cast<int>(pixelHeight));
    if (image.isNull()) {
        image = QImage(pixelSize, QImage::Format_ARGB32_Premultiplied);
        image.fill(qRgba(30, 90, 150, 255));
    } else if (image.size() != pixelSize) {
        return {};
    }

    QByteArray png;
    QBuffer buffer(&png);
    if (!buffer.open(QIODevice::WriteOnly) || !image.save(&buffer, "PNG")) {
        return {};
    }

    QByteArray request(ShellBridgeProtocol::RequestHeaderSize, '\0');
    request.replace(0, 8, QByteArrayLiteral("FSBRPNG1"));
    writeU32(request, 8, ShellBridgeProtocol::RequestHeaderSize);
    writeU32(request, 12, 0);
    writeU64(request, 16, captureId);
    writeU32(request, 24, static_cast<quint32>(logicalX));
    writeU32(request, 28, static_cast<quint32>(logicalY));
    writeU32(request, 32, logicalWidth);
    writeU32(request, 36, logicalHeight);
    writeU32(request, 40, pixelWidth);
    writeU32(request, 44, pixelHeight);
    writeU32(request, 48, 5);
    writeU32(request, 52, 4);
    writeU64(request, 56, static_cast<quint64>(png.size()));
    request.append(png);
    return request;
}

} // namespace

class ShellBridgeServerSmokeTest : public QObject
{
    Q_OBJECT

private slots:
    void initTestCase()
    {
        QVERIFY(QDBusConnection::sessionBus().registerService(
          QString::fromLatin1(ShellService)));
    }

    void init()
    {
        m_output.clear();
        const QString executable =
          qEnvironmentVariable("SNIPSNAP_TEST_EXECUTABLE");
        QVERIFY2(!executable.isEmpty(),
                 "test requires SNIPSNAP_TEST_EXECUTABLE");
        QVERIFY2(QFile::exists(executable), qPrintable(executable));

        m_runtime = new QTemporaryDir();
        QVERIFY(m_runtime->isValid());
        QVERIFY(QFile::setPermissions(
          m_runtime->path(),
          QFileDevice::ReadOwner | QFileDevice::WriteOwner
            | QFileDevice::ExeOwner));

        const QString home = m_runtime->path() + QStringLiteral("/home");
        const QString config = m_runtime->path() + QStringLiteral("/config");
        const QString cache = m_runtime->path() + QStringLiteral("/cache");
        const QString state = m_runtime->path() + QStringLiteral("/state");
        for (const QString& path : { home, config, cache, state }) {
            QVERIFY2(QDir().mkpath(path), qPrintable(path));
        }

        QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
        environment.insert(QStringLiteral("HOME"), home);
        environment.insert(QStringLiteral("XDG_CONFIG_HOME"), config);
        environment.insert(QStringLiteral("XDG_CACHE_HOME"), cache);
        environment.insert(QStringLiteral("XDG_STATE_HOME"), state);
        environment.insert(QStringLiteral("XDG_RUNTIME_DIR"), m_runtime->path());
        environment.insert(QStringLiteral("XDG_SESSION_TYPE"),
                           QStringLiteral("wayland"));
        environment.insert(QStringLiteral("XDG_CURRENT_DESKTOP"),
                           QStringLiteral("GNOME"));
        environment.insert(QStringLiteral("QT_QPA_PLATFORM"),
                           QStringLiteral("offscreen"));
        environment.insert(QStringLiteral("SNIPSNAP_CAPTURE_TRACE"),
                           QStringLiteral("1"));
        environment.insert(QStringLiteral("SNIPSNAP_CAPTURE_TRACE_RUN_ID"),
                           QStringLiteral("shell-bridge-smoke"));
        m_snipsnap.setProcessEnvironment(environment);
        m_snipsnap.setProcessChannelMode(QProcess::MergedChannels);
        m_snipsnap.setProgram(executable);
        m_snipsnap.start();
        QVERIFY2(m_snipsnap.waitForStarted(2'000),
                 qPrintable(m_snipsnap.errorString()));

        m_socketPath = m_runtime->path()
          + QStringLiteral("/snipsnap/gnome-shell-bridge-v1.sock");
        QVERIFY2(waitForBridgeSocket(),
                 qPrintable(QString::fromUtf8(m_snipsnap.readAll())));

        const QByteArray path = QFile::encodeName(m_socketPath);
        struct stat info{};
        QVERIFY(::lstat(path.constData(), &info) == 0);
        QVERIFY(S_ISSOCK(info.st_mode));
        QCOMPARE(info.st_mode & 0777, static_cast<mode_t>(0600));
    }

    void cleanup()
    {
        m_snipsnap.terminate();
        if (!m_snipsnap.waitForFinished(2'000)) {
            m_snipsnap.kill();
            m_snipsnap.waitForFinished(2'000);
        }
        delete m_runtime;
        m_runtime = nullptr;
    }

    void malformedRequestGetsBoundedError()
    {
        QLocalSocket socket;
        socket.connectToServer(m_socketPath);
        QVERIFY(socket.waitForConnected(1'000));
        QCOMPARE(socket.write(QByteArray(ShellBridgeProtocol::RequestHeaderSize,
                                        '\0')),
                 ShellBridgeProtocol::RequestHeaderSize);
        QVERIFY(socket.waitForBytesWritten(1'000));

        ShellBridgeProtocol::Ack ack;
        QString error;
        QVERIFY2(ShellBridgeProtocol::decodeAck(readAck(socket), &ack, &error),
                 qPrintable(error));
        QCOMPARE(ack.captureId, 0U);
        QCOMPARE(ack.status,
                 ShellBridgeProtocol::AckStatus::MalformedRequest);
        QCOMPARE(ack.daemonPid,
                 static_cast<quint32>(m_snipsnap.processId()));
    }

    void concurrentConnectionIsBusy()
    {
        QLocalSocket first;
        first.connectToServer(m_socketPath);
        QVERIFY(first.waitForConnected(1'000));
        QTest::qWait(50);

        QLocalSocket second;
        second.connectToServer(m_socketPath);
        QVERIFY(second.waitForConnected(1'000));
        ShellBridgeProtocol::Ack ack;
        QString error;
        QVERIFY2(ShellBridgeProtocol::decodeAck(readAck(second), &ack, &error),
                 qPrintable(error));
        QCOMPARE(ack.captureId, 0U);
        QCOMPARE(ack.status, ShellBridgeProtocol::AckStatus::Busy);
        first.abort();
    }

    void stagedEditorClosesOnPrecommitDisconnect()
    {
        constexpr quint64 CaptureId = 0x0102030405060708ULL;
        const QByteArray request = validRequest(CaptureId);
        QVERIFY(!request.isEmpty());

        QLocalSocket socket;
        socket.connectToServer(m_socketPath);
        QVERIFY(socket.waitForConnected(1'000));
        QCOMPARE(socket.write(request), request.size());
        QVERIFY(socket.waitForBytesWritten(1'000));

        ShellBridgeProtocol::Ack ack;
        QString error;
        QVERIFY2(ShellBridgeProtocol::decodeAck(readAck(socket), &ack, &error),
                 qPrintable(error));
        QCOMPARE(ack.captureId, CaptureId);
        QCOMPARE(ack.status,
                 ShellBridgeProtocol::AckStatus::RequestAccepted);
        QVERIFY2(ShellBridgeProtocol::decodeAck(readAck(socket), &ack, &error),
                 qPrintable(error));
        QCOMPARE(ack.captureId, CaptureId);
        QCOMPARE(ack.status, ShellBridgeProtocol::AckStatus::EditorReady);
        socket.abort();

        bool released = false;
        QElapsedTimer deadline;
        deadline.start();
        while (!released && deadline.elapsed() < 1'500) {
            QLocalSocket probe;
            probe.connectToServer(m_socketPath);
            if (!probe.waitForConnected(200)) {
                QTest::qWait(20);
                continue;
            }
            QCOMPARE(probe.write(QByteArray(
                       ShellBridgeProtocol::RequestHeaderSize, '\0')),
                     ShellBridgeProtocol::RequestHeaderSize);
            QVERIFY(probe.waitForBytesWritten(200));
            QVERIFY2(
              ShellBridgeProtocol::decodeAck(readAck(probe), &ack, &error),
              qPrintable(error));
            if (ack.status
                == ShellBridgeProtocol::AckStatus::MalformedRequest) {
                released = true;
            } else {
                QCOMPARE(ack.status, ShellBridgeProtocol::AckStatus::Busy);
                QTest::qWait(20);
            }
        }
        QVERIFY2(released, "staged editor did not release after disconnect");
    }

    void validRequestCommitsAndRetainsBusyEditor()
    {
        constexpr quint64 CaptureId = 0x1020304050607080ULL;
        const QByteArray request = validRequest(CaptureId);
        QVERIFY(!request.isEmpty());

        QLocalSocket socket;
        socket.connectToServer(m_socketPath);
        QVERIFY(socket.waitForConnected(1'000));
        QCOMPARE(socket.write(request), request.size());
        QVERIFY(socket.waitForBytesWritten(1'000));

        ShellBridgeProtocol::Ack ack;
        QString error;
        QVERIFY2(ShellBridgeProtocol::decodeAck(readAck(socket), &ack, &error),
                 qPrintable(error));
        QCOMPARE(ack.captureId, CaptureId);
        QCOMPARE(ack.status,
                 ShellBridgeProtocol::AckStatus::RequestAccepted);
        QCOMPARE(ack.daemonPid,
                 static_cast<quint32>(m_snipsnap.processId()));

        QVERIFY2(ShellBridgeProtocol::decodeAck(readAck(socket), &ack, &error),
                 qPrintable(error));
        QCOMPARE(ack.captureId, CaptureId);
        QCOMPARE(ack.status, ShellBridgeProtocol::AckStatus::EditorReady);

        const QByteArray commit = ShellBridgeProtocol::encodeCommit(CaptureId);
        QCOMPARE(socket.write(commit), commit.size());
        QVERIFY(socket.waitForBytesWritten(1'000));
        QVERIFY2(ShellBridgeProtocol::decodeAck(readAck(socket), &ack, &error),
                 qPrintable(error));
        QCOMPARE(ack.captureId, CaptureId);
        QCOMPARE(ack.status, ShellBridgeProtocol::AckStatus::CommitAccepted);

        QLocalSocket second;
        second.connectToServer(m_socketPath);
        QVERIFY(second.waitForConnected(1'000));
        QVERIFY2(ShellBridgeProtocol::decodeAck(readAck(second), &ack, &error),
                 qPrintable(error));
        QCOMPARE(ack.captureId, 0U);
        QCOMPARE(ack.status, ShellBridgeProtocol::AckStatus::Busy);
    }

    void externalAnnotatorSpawnsOnCommitAndFreesSlot()
    {
        // Restart the daemon with the annotator override pointing at a fake
        // that copies stdin to a file, then prove: EDITOR_READY without an
        // editor, spawn gated on commit, PNG delivered byte-perfect, and the
        // receiver slot free for a second capture while the annotator runs.
        const QString received =
          m_runtime->path() + QStringLiteral("/annotator-received.png");
        const QString script =
          m_runtime->path() + QStringLiteral("/fake-annotator.sh");
        {
            QFile file(script);
            QVERIFY(file.open(QIODevice::WriteOnly));
            file.write(QByteArrayLiteral("#!/bin/sh\nexec /bin/cat > \""));
            file.write(QFile::encodeName(received));
            file.write(QByteArrayLiteral("\"\n"));
        }
        QVERIFY(QFile::setPermissions(
          script,
          QFileDevice::ReadOwner | QFileDevice::WriteOwner
            | QFileDevice::ExeOwner));

        m_snipsnap.terminate();
        QVERIFY(m_snipsnap.waitForFinished(2'000));
        // The first daemon's socket file survives its exit; left in place it
        // satisfies waitForBridgeSocket() before the restarted daemon binds,
        // and the test then dials a dead socket.
        QFile::remove(m_socketPath);
        QProcessEnvironment environment = m_snipsnap.processEnvironment();
        environment.insert(QStringLiteral("SNIPSNAP_BRIDGE_ANNOTATOR"),
                           script);
        m_snipsnap.setProcessEnvironment(environment);
        m_output.clear();
        m_snipsnap.start();
        QVERIFY2(m_snipsnap.waitForStarted(2'000),
                 qPrintable(m_snipsnap.errorString()));
        QVERIFY2(waitForBridgeSocket(),
                 qPrintable(QString::fromUtf8(m_snipsnap.readAll())));

        constexpr quint64 CaptureId = 0x2233445566778899ULL;
        const QImage landmarks = landmarkImage(QSize(125, 100));
        const QByteArray request = validRequest(
          CaptureId, -40, -24, 100, 80, 125, 100, landmarks);
        QVERIFY(!request.isEmpty());

        QLocalSocket socket;
        socket.connectToServer(m_socketPath);
        QVERIFY(socket.waitForConnected(1'000));
        QCOMPARE(socket.write(request), request.size());
        QVERIFY(socket.waitForBytesWritten(1'000));

        ShellBridgeProtocol::Ack ack;
        QString error;
        QVERIFY2(ShellBridgeProtocol::decodeAck(readAck(socket), &ack, &error),
                 qPrintable(error));
        QCOMPARE(ack.status,
                 ShellBridgeProtocol::AckStatus::RequestAccepted);
        QVERIFY2(ShellBridgeProtocol::decodeAck(readAck(socket), &ack, &error),
                 qPrintable(error));
        QCOMPARE(ack.captureId, CaptureId);
        QCOMPARE(ack.status, ShellBridgeProtocol::AckStatus::EditorReady);

        // No spawn may happen before the commit gates it.
        QTest::qWait(150);
        QVERIFY(!QFile::exists(received));

        const QByteArray commit = ShellBridgeProtocol::encodeCommit(CaptureId);
        QCOMPARE(socket.write(commit), commit.size());
        QVERIFY(socket.waitForBytesWritten(1'000));
        QVERIFY2(ShellBridgeProtocol::decodeAck(readAck(socket), &ack, &error),
                 qPrintable(error));
        QCOMPARE(ack.status, ShellBridgeProtocol::AckStatus::CommitAccepted);

        QImage delivered;
        QElapsedTimer deadline;
        deadline.start();
        while (deadline.elapsed() < 3'000) {
            if (QFile::exists(received) && delivered.load(received, "PNG")
                && !delivered.isNull()) {
                break;
            }
            QTest::qWait(25);
        }
        QCOMPARE(delivered.size(), QSize(125, 100));
        QCOMPARE(delivered.pixelColor(0, 0), QColor(240, 10, 20));
        QCOMPARE(delivered.pixelColor(124, 99), QColor(230, 220, 40));

        // External mode holds no editor, so the receiver accepts the next
        // capture while the annotator window is still open.
        QLocalSocket second;
        second.connectToServer(m_socketPath);
        QVERIFY(second.waitForConnected(1'000));
        const QByteArray next = validRequest(CaptureId + 1);
        QCOMPARE(second.write(next), next.size());
        QVERIFY(second.waitForBytesWritten(1'000));
        QVERIFY2(ShellBridgeProtocol::decodeAck(readAck(second), &ack, &error),
                 qPrintable(error));
        QCOMPARE(ack.captureId, CaptureId + 1);
        QCOMPARE(ack.status,
                 ShellBridgeProtocol::AckStatus::RequestAccepted);
        second.abort();
    }

    void regionEditorPreservesFractionalSourceMapping()
    {
        constexpr quint64 CaptureId = 0x1122334455667788ULL;
        constexpr qint32 LogicalX = 3;
        constexpr qint32 LogicalY = 5;
        constexpr quint32 LogicalWidth = 101;
        constexpr quint32 LogicalHeight = 81;
        constexpr quint32 PixelWidth = 127;
        constexpr quint32 PixelHeight = 102;
        const QSize pixelSize(static_cast<int>(PixelWidth),
                              static_cast<int>(PixelHeight));
        const QImage landmarks = landmarkImage(pixelSize);

        const QByteArray request = validRequest(CaptureId,
                                                LogicalX,
                                                LogicalY,
                                                LogicalWidth,
                                                LogicalHeight,
                                                PixelWidth,
                                                PixelHeight,
                                                landmarks);
        QVERIFY(!request.isEmpty());

        // At 5/4 scale, deriving the frame from the outward-rounded pixel
        // crop would produce 102x82. The protocol selection is exactly
        // 101x81 logical pixels and is the authoritative frame geometry.
        QCOMPARE(static_cast<int>(std::ceil(PixelWidth / 1.25)), 102);
        QCOMPARE(static_cast<int>(std::ceil(PixelHeight / 1.25)), 82);

        QLocalSocket socket;
        socket.connectToServer(m_socketPath);
        QVERIFY(socket.waitForConnected(1'000));
        QCOMPARE(socket.write(request), request.size());
        QVERIFY(socket.waitForBytesWritten(1'000));

        ShellBridgeProtocol::Ack ack;
        QString error;
        QVERIFY2(ShellBridgeProtocol::decodeAck(readAck(socket), &ack, &error),
                 qPrintable(error));
        QCOMPARE(ack.captureId, CaptureId);
        QCOMPARE(ack.status,
                 ShellBridgeProtocol::AckStatus::RequestAccepted);
        QVERIFY2(ShellBridgeProtocol::decodeAck(readAck(socket), &ack, &error),
                 qPrintable(error));
        QCOMPARE(ack.captureId, CaptureId);
        QCOMPARE(ack.status, ShellBridgeProtocol::AckStatus::EditorReady);

        const QJsonObject firstPaint = waitForTraceEvent(
          QStringLiteral("capture_widget_paint_completed"));
        QVERIFY2(!firstPaint.isEmpty(), qPrintable(QString::fromUtf8(m_output)));
        const QJsonObject fields =
          firstPaint.value(QStringLiteral("fields")).toObject();
        QCOMPARE(fields.value(QStringLiteral("width")).toInt(),
                 static_cast<int>(LogicalWidth));
        QCOMPARE(fields.value(QStringLiteral("height")).toInt(),
                 static_cast<int>(LogicalHeight));
    }

    void cleanupTestCase()
    {
        QDBusConnection::sessionBus().unregisterService(
          QString::fromLatin1(ShellService));
    }

private:
    bool waitForBridgeSocket()
    {
        const QByteArray path = QFile::encodeName(m_socketPath);
        struct stat info{};
        QElapsedTimer deadline;
        deadline.start();
        while (deadline.elapsed() < 3'000
               && m_snipsnap.state() != QProcess::NotRunning) {
            if (::lstat(path.constData(), &info) == 0
                && S_ISSOCK(info.st_mode)
                && (info.st_mode & 0777) == static_cast<mode_t>(0600)) {
                return true;
            }
            QTest::qWait(20);
        }
        return false;
    }

    QJsonObject waitForTraceEvent(const QString& expectedEvent)
    {
        QElapsedTimer deadline;
        deadline.start();
        while (deadline.elapsed() < 1'000) {
            m_output.append(m_snipsnap.readAll());
            const QList<QByteArray> lines = m_output.split('\n');
            for (const QByteArray& line : lines) {
                const qsizetype marker = line.indexOf(
                  QByteArray(CaptureTrace::StderrPrefix));
                if (marker < 0) {
                    continue;
                }
                QJsonParseError parseError;
                const QJsonDocument document = QJsonDocument::fromJson(
                  line.mid(marker + QByteArray(CaptureTrace::StderrPrefix).size()),
                  &parseError);
                if (parseError.error == QJsonParseError::NoError
                    && document.isObject()
                    && document.object()
                           .value(QStringLiteral("event"))
                           .toString()
                      == expectedEvent) {
                    return document.object();
                }
            }
            m_snipsnap.waitForReadyRead(20);
        }
        m_output.append(m_snipsnap.readAll());
        return {};
    }

    QTemporaryDir* m_runtime{ nullptr };
    QProcess m_snipsnap;
    QString m_socketPath;
    QByteArray m_output;
};

QTEST_GUILESS_MAIN(ShellBridgeServerSmokeTest)

#include "shell_bridge_server_smoke_test.moc"
