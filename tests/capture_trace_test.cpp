// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#include "utils/capturetrace.h"

#include <QJsonDocument>
#include <QJsonObject>
#include <QTest>

class CaptureTraceTest : public QObject
{
    Q_OBJECT

private slots:
    void cleanup()
    {
        qunsetenv("SNIPSNAP_CAPTURE_TRACE");
        qunsetenv("SNIPSNAP_CAPTURE_TRACE_RUN_ID");
        CaptureTrace::finish(QStringLiteral("test_cleanup"));
    }

    void disabledByDefault()
    {
        qunsetenv("SNIPSNAP_CAPTURE_TRACE");
        QVERIFY(!CaptureTrace::isEnabled());
    }

    void recognizesEnabledAndDisabledValues_data()
    {
        QTest::addColumn<QByteArray>("value");
        QTest::addColumn<bool>("enabled");

        QTest::newRow("one") << QByteArray("1") << true;
        QTest::newRow("true") << QByteArray("true") << true;
        QTest::newRow("stderr") << QByteArray("stderr") << true;
        QTest::newRow("file") << QByteArray("file") << true;
        QTest::newRow("zero") << QByteArray("0") << false;
        QTest::newRow("false") << QByteArray("false") << false;
        QTest::newRow("off") << QByteArray("off") << false;
        QTest::newRow("no") << QByteArray("no") << false;
    }

    void recognizesEnabledAndDisabledValues()
    {
        QFETCH(QByteArray, value);
        QFETCH(bool, enabled);
        qputenv("SNIPSNAP_CAPTURE_TRACE", value);
        QCOMPARE(CaptureTrace::isEnabled(), enabled);
    }

    void serializesStableJsonContract()
    {
        const CaptureTrace::Record record{
            QStringLiteral("baseline-20260718"),
            7,
            QStringLiteral("image_decode_finished"),
            1234,
            { { QStringLiteral("height"), 2160 },
              { QStringLiteral("ok"), true },
              { QStringLiteral("width"), 3840 } }
        };

        const QByteArray encoded = CaptureTrace::serialize(record);
        QJsonParseError parseError;
        const QJsonDocument document =
          QJsonDocument::fromJson(encoded, &parseError);
        QCOMPARE(parseError.error, QJsonParseError::NoError);
        QVERIFY(document.isObject());

        const QJsonObject object = document.object();
        QCOMPARE(object.value(QStringLiteral("schema")).toString(),
                 QLatin1String(CaptureTrace::Schema));
        QCOMPARE(object.value(QStringLiteral("run_id")).toString(),
                 QStringLiteral("baseline-20260718"));
        QCOMPARE(object.value(QStringLiteral("capture_id")).toInteger(), 7);
        QCOMPARE(object.value(QStringLiteral("event")).toString(),
                 QStringLiteral("image_decode_finished"));
        QCOMPARE(object.value(QStringLiteral("elapsed_us")).toInteger(),
                 1234);
        QCOMPARE(object.value(QStringLiteral("fields"))
                   .toObject()
                   .value(QStringLiteral("width"))
                   .toInt(),
                 3840);
        QVERIFY(!encoded.contains('\n'));
        QCOMPARE(QJsonDocument(object).toJson(QJsonDocument::Compact),
                 encoded);
    }

    void activeTimelineRejectsReentrantBegin()
    {
        qputenv("SNIPSNAP_CAPTURE_TRACE", "stderr");
        qputenv("SNIPSNAP_CAPTURE_TRACE_RUN_ID", "lifecycle-test");
        CaptureTrace::initializeProcessStart();

        const CaptureTrace::TimelineStart firstStart =
          CaptureTrace::captureRequestStart();
        QVERIFY(CaptureTrace::begin(QStringLiteral("first"), firstStart));
        const CaptureTrace::TimelineStart reentrantStart =
          CaptureTrace::captureRequestStart();
        QVERIFY(!CaptureTrace::begin(QStringLiteral("reentrant"),
                                     reentrantStart));
        CaptureTrace::mark(QStringLiteral("first_continues"));
        CaptureTrace::finish(QStringLiteral("capture_cancelled"));
        const CaptureTrace::TimelineStart nextStart =
          CaptureTrace::captureRequestStart();
        QVERIFY(CaptureTrace::begin(QStringLiteral("next"), nextStart));
        CaptureTrace::finish(QStringLiteral("capture_cancelled"));
    }
};

QTEST_GUILESS_MAIN(CaptureTraceTest)

#include "capture_trace_test.moc"
