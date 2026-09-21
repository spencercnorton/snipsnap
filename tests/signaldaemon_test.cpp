#include "core/signaldaemon.h"

#include <QSignalSpy>
#include <QtTest>
#include <csignal>

class SignalDaemonTest : public QObject
{
    Q_OBJECT

private slots:
    void deliversInterrupt()
    {
        SignalDaemon daemon;
        QSignalSpy spy(&daemon, &SignalDaemon::signalReceived);

        SignalDaemon::intSignalHandler(SIGINT);

        QTRY_COMPARE_WITH_TIMEOUT(spy.size(), 1, 1000);
        QCOMPARE(spy.takeFirst().at(0).toInt(), SIGINT);
    }

    void deliversTermination()
    {
        SignalDaemon daemon;
        QSignalSpy spy(&daemon, &SignalDaemon::signalReceived);

        SignalDaemon::termSignalHandler(SIGTERM);

        QTRY_COMPARE_WITH_TIMEOUT(spy.size(), 1, 1000);
        QCOMPARE(spy.takeFirst().at(0).toInt(), SIGTERM);
    }

    void repeatedSignalsNeverBlockTheHandler()
    {
        SignalDaemon daemon;
        QSignalSpy spy(&daemon, &SignalDaemon::signalReceived);

        for (int i = 0; i < 300000; ++i) {
            SignalDaemon::termSignalHandler(SIGTERM);
        }

        QTRY_VERIFY_WITH_TIMEOUT(!spy.isEmpty(), 1000);
    }
};

QTEST_GUILESS_MAIN(SignalDaemonTest)

#include "signaldaemon_test.moc"
