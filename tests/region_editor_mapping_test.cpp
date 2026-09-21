// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#include "tools/capturesourcemapping.h"
#include "widgets/capture/magnifierwidget.h"
#include "widgets/panel/colorgrabwidget.h"

#include <QApplication>
#include <QColor>
#include <QGraphicsProxyWidget>
#include <QGraphicsScene>
#include <QGraphicsView>
#include <QImage>
#include <QPainter>
#include <QRectF>
#include <QTest>
#include <QWidget>

namespace
{

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

QImage renderEditorPreview(const QImage& source,
                           const CaptureSourceMapping& mapping,
                           const QSize& logicalSize)
{
    constexpr int PreviewDevicePixelRatio = 5;
    QImage preview(logicalSize * PreviewDevicePixelRatio,
                   QImage::Format_ARGB32_Premultiplied);
    preview.setDevicePixelRatio(PreviewDevicePixelRatio);
    preview.fill(Qt::transparent);

    QPainter painter(&preview);
    painter.setRenderHint(QPainter::SmoothPixmapTransform, false);
    painter.drawImage(mapping.contentOrigin(), source);
    return preview;
}

} // namespace

class RegionEditorMappingTest : public QObject
{
    Q_OBJECT

private slots:
    void fractionalPhasePreservesCoordinateSpaces()
    {
        constexpr qreal Scale = 1.25;
        const QRect logicalSourceRect(3, 5, 101, 81);
        const QSize pixelSize(127, 102);
        const CaptureSourceMapping mapping(
          logicalSourceRect, Scale, pixelSize);

        QVERIFY(mapping.isValid());
        QVERIFY(!CaptureSourceMapping(logicalSourceRect, Scale, QSize())
                   .isValid());

        QCOMPARE(mapping.leadingPixelPhase(), QPointF(0.75, 0.25));
        QVERIFY(qAbs(mapping.contentOrigin().x() + 0.6) < 0.000'001);
        QVERIFY(qAbs(mapping.contentOrigin().y() + 0.2) < 0.000'001);
        QVERIFY(qAbs(mapping.commitTranslation().x() - 0.6) < 0.000'001);
        QVERIFY(qAbs(mapping.commitTranslation().y() - 0.2) < 0.000'001);

        const QRect fullLogicalRect(QPoint(0, 0), logicalSourceRect.size());
        QCOMPARE(mapping.pixelRect(fullLogicalRect),
                 QRect(QPoint(0, 0), pixelSize));
        QCOMPARE(mapping.logicalExportRect(fullLogicalRect),
                 logicalSourceRect);

        // Protocol v1 allows a one-pixel compositor rounding tolerance. The
        // exact logical editor still owns those surplus trailing edge pixels.
        const CaptureSourceMapping surplusMapping(
          logicalSourceRect, Scale, QSize(128, 103));
        QCOMPARE(surplusMapping.pixelRect(fullLogicalRect),
                 QRect(0, 0, 128, 103));

        const QRect logicalSubselection(7, 11, 23, 17);
        QCOMPARE(mapping.pixelRect(logicalSubselection),
                 QRect(9, 14, 30, 22));
        QCOMPARE(mapping.logicalExportRect(logicalSubselection),
                 QRect(10, 16, 23, 17));
    }

    void fractionalPhaseAlignsContentAndAnnotations()
    {
        constexpr qreal Scale = 1.25;
        const QRect logicalSourceRect(3, 5, 101, 81);
        const QSize pixelSize(127, 102);
        const CaptureSourceMapping mapping(
          logicalSourceRect, Scale, pixelSize);
        const QRect fullLogicalRect(QPoint(0, 0), logicalSourceRect.size());
        const QImage landmarks = landmarkImage(pixelSize);

        // Copying the mapped full selection must retain every outward edge
        // pixel, including pixels only partially inside the logical region.
        const QImage copied = landmarks.copy(mapping.pixelRect(fullLogicalRect));
        QCOMPARE(copied.size(), landmarks.size());
        QCOMPARE(copied.pixelColor(0, 0), landmarks.pixelColor(0, 0));
        QCOMPARE(copied.pixelColor(copied.width() - 1, 0),
                 landmarks.pixelColor(landmarks.width() - 1, 0));
        QCOMPARE(copied.pixelColor(0, copied.height() - 1),
                 landmarks.pixelColor(0, landmarks.height() - 1));
        QCOMPARE(copied.pixelColor(copied.width() - 1,
                                   copied.height() - 1),
                 landmarks.pixelColor(landmarks.width() - 1,
                                      landmarks.height() - 1));

        QImage source = landmarks;
        source.setDevicePixelRatio(Scale);
        const QImage preview = renderEditorPreview(
          source, mapping, logicalSourceRect.size());
        // This phase-sensitive editor point maps to source pixel (1, 1).
        // Drawing the crop at (0, 0) would show source pixel (0, 0).
        QCOMPARE(preview.pixelColor(1, 3), source.pixelColor(1, 1));
        QCOMPARE(preview.pixelColor(0, 0), source.pixelColor(0, 0));
        QCOMPARE(preview.pixelColor(preview.width() - 1, 0),
                 source.pixelColor(source.width() - 1, 0));
        QCOMPARE(preview.pixelColor(0, preview.height() - 1),
                 source.pixelColor(0, source.height() - 1));
        QCOMPARE(preview.pixelColor(preview.width() - 1,
                                    preview.height() - 1),
                 source.pixelColor(source.width() - 1,
                                   source.height() - 1));

        // Tool coordinates originate in the exact logical editor. Translating
        // by the inverse phase before committing keeps redraw aligned.
        const QColor annotationColor(245, 20, 235);
        QImage annotated = source;
        {
            QPainter painter(&annotated);
            painter.translate(mapping.commitTranslation());
            painter.fillRect(QRectF(20.0, 10.0, 8.0, 8.0),
                             annotationColor);
        }
        const QImage annotatedPreview = renderEditorPreview(
          annotated, mapping, logicalSourceRect.size());
        QCOMPARE(annotatedPreview.pixelColor(139, 70), annotationColor);
    }

    void fractionalPhaseMapsEverySourceConsumer()
    {
        constexpr qreal Scale = 1.25;
        const CaptureSourceMapping mapping(
          QRect(3, 5, 101, 81), Scale, QSize(127, 102));

        // Magnifier and color-grab consumers both map the editor-local cursor
        // through this point contract before sampling the raw source image.
        QCOMPARE(mapping.sourcePixel(QPointF(20, 10)), QPoint(25, 12));
        QCOMPARE(mapping.sourcePixelF(QPointF(20, 10)),
                 QPointF(25.75, 12.75));
        QCOMPARE(mapping.editorPointForSourcePixel(QPointF(25.75, 12.75)),
                 QPointF(20, 10));

        // Invert and Pixelate use this outward source rectangle. Multiplying
        // the logical top-left by DPR without phase would start at x=1.
        QCOMPARE(mapping.pixelRect(QRect(1, 3, 4, 4)),
                 QRect(2, 4, 5, 5));
        QCOMPARE(mapping.editorRectForSourcePixelRect(QRect(2, 4, 5, 5)),
                 QRectF(QPointF(1.0, 3.0), QSizeF(4.0, 4.0)));

        // A selection whose edges are not on source-pixel boundaries must
        // draw back to the exact outward raw boundaries, not compress those
        // raw pixels into the original integer logical rectangle.
        QCOMPARE(mapping.pixelRect(QRect(2, 2, 4, 4)),
                 QRect(3, 2, 6, 6));
        QCOMPARE(mapping.editorRectForSourcePixelRect(QRect(3, 2, 6, 6)),
                 QRectF(QPointF(1.8, 1.4), QSizeF(4.8, 4.8)));

        // Grid rendering and snapping share the absolute compositor pixel
        // lattice rather than the independently positioned editor window.
        QCOMPARE(mapping.snapEditorPointToPixelGrid(QPoint(20, 10), 10),
                 QPoint(21, 11));
        QCOMPARE(mapping.snapEditorPointToPixelGrid(QPoint(0, 0), 10),
                 QPoint(5, 3));
        QCOMPARE(mapping.editorPointForAbsolutePixel(QPointF(10, 10)),
                 QPointF(5, 3));
        QCOMPARE(mapping.absolutePixelF(QPointF(5, 3)), QPointF(10, 10));

        // Invert/Pixelate padding is clipped in logical editor space before
        // source mapping, not against the larger raw 127x102 pixel rectangle.
        QCOMPARE(mapping.pixelRect(QRect(99, 79, 8, 8)),
                 QRect(124, 99, 3, 3));
    }

    void fourThirdsCanonicalScaleHasNoFalsePhase()
    {
        const qreal scale = qreal(4.0) / qreal(3.0);
        const QRect logicalSourceRect(3, 5, 9, 6);
        const CaptureSourceMapping mapping(
          logicalSourceRect, scale, QSize(12, 9));

        QCOMPARE(mapping.leadingPixelPhase().x(), 0.0);
        QCOMPARE(mapping.sourcePixelF(QPointF(3, 0)).x(), 4.0);
        QCOMPARE(mapping.sourcePixel(QPointF(3, 0)).x(), 4);
        QCOMPARE(mapping.pixelRect(QRect(QPoint(0, 0), logicalSourceRect.size())),
                 QRect(0, 0, 12, 9));
    }

    void negativeLogicalOriginUsesOutwardLeadingPhase()
    {
        const CaptureSourceMapping mapping(
          QRect(-3, -5, 101, 81), 1.25, QSize(127, 102));

        QCOMPARE(mapping.leadingPixelPhase(), QPointF(0.25, 0.75));
        QCOMPARE(mapping.absolutePixelF(QPointF(0, 0)),
                 QPointF(-3.75, -6.25));
        QCOMPARE(mapping.sourcePixelF(QPointF(0, 0)), QPointF(0.25, 0.75));
        QCOMPARE(mapping.sourcePixel(QPointF(0, 0)), QPoint(0, 0));
        QCOMPARE(mapping.pixelRect(mapping.editorBounds()),
                 mapping.sourcePixelBounds());
    }

    void magnifierAndColorGrabUseMappedSourcePixels()
    {
        const CaptureSourceMapping mapping(
          QRect(3, 5, 101, 81), 1.25, QSize(127, 102));

        QCOMPARE(MagnifierWidget::sourceRectForEditorPoint(
                   mapping, QPointF(20, 10), 8, false),
                 QRect(17, 4, 17, 17));
        QCOMPARE(MagnifierWidget::sourceRectForEditorPoint(
                   mapping, QPointF(20, 10), 8, true),
                 QRect(25, 12, 17, 17));

        QWidget captureSurface;
        captureSurface.setWindowFlags(Qt::FramelessWindowHint);
        captureSurface.setGeometry(100, 200, 101, 81);
        const QPoint globalPoint = captureSurface.mapToGlobal(QPoint(20, 10));
        QCOMPARE(ColorGrabWidget::mappedSourcePixelForGlobalPoint(
                   mapping, &captureSurface, globalPoint),
                 QPoint(25, 12));
        QCOMPARE(ColorGrabWidget::mappedSourcePixelForGlobalPoint(
                   mapping,
                   &captureSurface,
                   captureSurface.mapToGlobal(QPoint(101, 81))),
                 QPoint(-1, -1));
    }

    void proxyScalePreservesGlobalToEditorMapping()
    {
        const CaptureSourceMapping mapping(
          QRect(3, 5, 101, 81), 1.25, QSize(127, 102));

        QGraphicsScene scene;
        auto* captureSurface = new QWidget;
        captureSurface->resize(mapping.editorBounds().size());
        auto* magnifierSurface = new QWidget(captureSurface);
        magnifierSurface->setGeometry(captureSurface->rect());
        auto* proxy = scene.addWidget(captureSurface);
        scene.setSceneRect(QRectF(QPointF(0, 0),
                                  QSizeF(mapping.editorBounds().size())));

        QGraphicsView view(&scene);
        view.setFrameShape(QFrame::NoFrame);
        view.setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
        view.setVerticalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
        view.resize(51, 41);
        view.setTransform(QTransform::fromScale(0.5, 0.5));
        view.centerOn(scene.sceneRect().center());
        view.show();
        QApplication::processEvents();

        const QPoint editorPoint(20, 10);
        const QPoint viewPoint =
          view.mapFromScene(proxy->mapToScene(editorPoint));
        const QPoint globalPoint = view.viewport()->mapToGlobal(viewPoint);

        // Qt's QWidget global mapping traverses the QGraphicsProxyWidget and
        // QGraphicsView transform. ColorGrab therefore receives full canvas
        // coordinates even when the native viewport is scaled to one output.
        QCOMPARE(captureSurface->mapFromGlobal(globalPoint), editorPoint);
        QCOMPARE(magnifierSurface->mapFromGlobal(globalPoint), editorPoint);
        QCOMPARE(ColorGrabWidget::mappedSourcePixelForGlobalPoint(
                   mapping, captureSurface, globalPoint),
                 QPoint(25, 12));
    }
};

QTEST_MAIN(RegionEditorMappingTest)

#include "region_editor_mapping_test.moc"
