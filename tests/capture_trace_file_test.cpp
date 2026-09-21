// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#include "config/cacheutils.h"
#include "utils/capturetrace.h"

#include <QCoreApplication>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTemporaryDir>
#include <QTest>
#include <sys/stat.h>

namespace {

int permissions(const QString& path)
{
    struct stat metadata{};
    if (::stat(QFile::encodeName(path).constData(), &metadata) != 0) {
        return -1;
    }
    return static_cast<int>(metadata.st_mode & 0777);
}

} // namespace

class CaptureTraceFileTest : public QObject
{
    Q_OBJECT

private slots:
    void writesCompleteOwnerOnlyJsonLines()
    {
        CaptureTrace::initializeProcessStart();
        const CaptureTrace::TimelineStart start = CaptureTrace::processStart();
        QVERIFY(CaptureTrace::begin(QStringLiteral("file_test"), start));
        CaptureTrace::mark(QStringLiteral("capture_widget_paint_completed"));
        CaptureTrace::finish(QStringLiteral("capture_cancelled"));

        const QString path =
          getCachePath() + QStringLiteral("/capture-trace.jsonl");
        QCOMPARE(permissions(path), 0600);

        QFile file(path);
        QVERIFY(file.open(QIODevice::ReadOnly | QIODevice::Text));
        QList<QByteArray> lines = file.readAll().split('\n');
        QCOMPARE(lines.takeLast(), QByteArray());
        QCOMPARE(lines.size(), 4);
        const QStringList expectedEvents{
            QStringLiteral("process_started"),
            QStringLiteral("capture_requested"),
            QStringLiteral("capture_widget_paint_completed"),
            QStringLiteral("capture_cancelled"),
        };
        qint64 previousElapsed = -1;
        for (qsizetype index = 0; index < lines.size(); ++index) {
            QJsonParseError error;
            const QJsonDocument document =
              QJsonDocument::fromJson(lines.at(index), &error);
            QCOMPARE(error.error, QJsonParseError::NoError);
            QVERIFY(document.isObject());
            const QJsonObject object = document.object();
            QCOMPARE(object.value(QStringLiteral("schema")).toString(),
                     QLatin1String(CaptureTrace::Schema));
            QCOMPARE(object.value(QStringLiteral("run_id")).toString(),
                     QStringLiteral("file-io-test"));
            QCOMPARE(object.value(QStringLiteral("event")).toString(),
                     expectedEvents.at(index));
            const qint64 elapsed =
              object.value(QStringLiteral("elapsed_us")).toInteger();
            QVERIFY(elapsed >= previousElapsed);
            previousElapsed = elapsed;
            if (index == 1) {
                const QJsonObject fields =
                  object.value(QStringLiteral("fields")).toObject();
                QCOMPARE(fields.value(QStringLiteral("source")).toString(),
                         QStringLiteral("file_test"));
                QCOMPARE(fields.value(QStringLiteral("origin")).toString(),
                         QStringLiteral("process_start"));
            }
        }
    }
};

int main(int argc, char* argv[])
{
    QTemporaryDir cacheRoot;
    if (!cacheRoot.isValid()) {
        return 1;
    }
    qputenv("XDG_CACHE_HOME", cacheRoot.path().toUtf8());
    qputenv("SNIPSNAP_CAPTURE_TRACE", "file");
    qputenv("SNIPSNAP_CAPTURE_TRACE_RUN_ID", "file-io-test");

    QCoreApplication application(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("snipsnap-trace-test"));
    QCoreApplication::setOrganizationName(QStringLiteral("snipsnap"));

    CaptureTraceFileTest test;
    return QTest::qExec(&test, argc, argv);
}

#include "capture_trace_file_test.moc"
