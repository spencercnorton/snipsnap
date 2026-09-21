// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#include "config/cacheutils.h"
#include "utils/capturetrace.h"

#include <QCoreApplication>
#include <QFile>
#include <QFileInfo>
#include <QTemporaryDir>
#include <QTest>
#include <utility>

class CaptureTraceSymlinkTest : public QObject
{
    Q_OBJECT

private slots:
    void refusesSymlinkOutput()
    {
        const QString targetPath =
          m_root + QStringLiteral("/must-not-change.txt");
        QFile target(targetPath);
        QVERIFY(target.open(QIODevice::WriteOnly));
        QCOMPARE(target.write("sentinel"), qint64(8));
        target.close();

        const QString tracePath =
          getCachePath() + QStringLiteral("/capture-trace.jsonl");
        QVERIFY(QFile::link(targetPath, tracePath));
        QVERIFY(QFileInfo(tracePath).isSymLink());

        CaptureTrace::initializeProcessStart();
        const CaptureTrace::TimelineStart start = CaptureTrace::processStart();
        QVERIFY(!CaptureTrace::begin(QStringLiteral("symlink_test"), start));

        QVERIFY(target.open(QIODevice::ReadOnly));
        QCOMPARE(target.readAll(), QByteArray("sentinel"));
    }

public:
    explicit CaptureTraceSymlinkTest(QString root)
      : m_root(std::move(root))
    {}

private:
    QString m_root;
};

int main(int argc, char* argv[])
{
    QTemporaryDir cacheRoot;
    if (!cacheRoot.isValid()) {
        return 1;
    }
    qputenv("XDG_CACHE_HOME", cacheRoot.path().toUtf8());
    qputenv("SNIPSNAP_CAPTURE_TRACE", "file");
    qputenv("SNIPSNAP_CAPTURE_TRACE_RUN_ID", "symlink-test");

    QCoreApplication application(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("snipsnap-trace-test"));
    QCoreApplication::setOrganizationName(QStringLiteral("snipsnap"));

    CaptureTraceSymlinkTest test(cacheRoot.path());
    return QTest::qExec(&test, argc, argv);
}

#include "capture_trace_symlink_test.moc"
