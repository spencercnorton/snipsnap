#include "utils/colorutils.h"

#include <QtTest>

class ColorUtilsTest : public QObject
{
    Q_OBJECT

private slots:
    void choosesTheHigherContrastForeground_data()
    {
        QTest::addColumn<QColor>("background");
        QTest::addColumn<bool>("expectsWhiteForeground");

        QTest::newRow("black") << QColor(Qt::black) << true;
        QTest::newRow("white") << QColor(Qt::white) << false;
        QTest::newRow("red") << QColor(Qt::red) << false;
        QTest::newRow("green") << QColor(Qt::green) << false;
        QTest::newRow("blue") << QColor(Qt::blue) << true;
        QTest::newRow("gray-below-crossover") << QColor("#757575") << true;
        QTest::newRow("gray-above-crossover") << QColor("#767676") << false;
        QTest::newRow("snipsnap-purple") << QColor(116, 0, 150) << true;
    }

    void choosesTheHigherContrastForeground()
    {
        QFETCH(QColor, background);
        QFETCH(bool, expectsWhiteForeground);

        QCOMPARE(ColorUtils::colorIsDark(background), expectsWhiteForeground);
    }
};

QTEST_GUILESS_MAIN(ColorUtilsTest)

#include "colorutils_test.moc"
