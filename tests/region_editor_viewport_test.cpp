// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#include "widgets/capture/regioneditorviewport.h"

#include <QTest>

class RegionEditorViewportTest : public QObject
{
    Q_OBJECT

private slots:
    void fitsWholeCanvasInsideOneOutput()
    {
        QCOMPARE(regionEditorViewportSize({ 2521, 601 }, { 480, 480 }),
                 QSize(480, 114));
        QCOMPARE(regionEditorViewportSize({ 3713, 521 }, { 1280, 720 }),
                 QSize(1280, 179));
        QCOMPARE(regionEditorViewportSize({ 11520, 2160 }, { 3840, 2160 }),
                 QSize(3840, 720));
    }

    void neverUpscalesOrAcceptsEmptyGeometry()
    {
        QCOMPARE(regionEditorViewportSize({ 320, 200 }, { 1920, 1080 }),
                 QSize(320, 200));
        QVERIFY(regionEditorViewportSize({ 0, 200 }, { 1920, 1080 }).isEmpty());
        QVERIFY(regionEditorViewportSize({ 320, 200 }, { 0, 1080 }).isEmpty());
        QVERIFY(regionEditorViewportSize({ -1, 200 }, { 1920, 1080 }).isEmpty());
        QCOMPARE(regionEditorFitScale({ 320, 200 }, { 1920, 1080 }), 1.0);
        QCOMPARE(regionEditorFitScale({ 0, 200 }, { 1920, 1080 }), 0.0);
    }

    void preservesTheScenePointUnderTheZoomAnchor()
    {
        QCOMPARE(regionEditorAnchoredCenter(
                   QPointF(600, 600), QPointF(380, 440), QPointF(628, 688)),
                 QPointF(352, 352));
    }
};

QTEST_GUILESS_MAIN(RegionEditorViewportTest)

#include "region_editor_viewport_test.moc"
