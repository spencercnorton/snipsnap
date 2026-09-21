// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#include "tools/capturesourcemapping.h"
#include "tools/invert/inverttool.h"
#include "tools/pixelate/pixelaterenderer.h"

#include <QImage>
#include <QObject>
#include <QPainter>
#include <QPixmap>
#include <QTest>

#include <utility>

// AbstractTwoPointTool's meta-object registers CaptureContext and therefore
// default-constructs its CaptureRequest member. This test does not exercise
// request/configuration behavior, so keep that dependency isolated from the
// rendering fixture.
CaptureRequest::CaptureRequest(CaptureRequest::CaptureMode mode,
                               const uint delay,
                               QVariant data,
                               CaptureRequest::ExportTask tasks)
  : m_mode(mode)
  , m_delay(delay)
  , m_tasks(tasks)
  , m_data(std::move(data))
  , m_selectedMonitor(-1)
  , m_hasSelectedMonitor(false)
{}

namespace
{

QImage patternedImage(const QSize& size)
{
    QImage image(size, QImage::Format_ARGB32_Premultiplied);
    for (int y = 0; y < image.height(); ++y) {
        for (int x = 0; x < image.width(); ++x) {
            image.setPixelColor(
              x,
              y,
              QColor(20 + (x * 19 + y * 7) % 200,
                     20 + (x * 5 + y * 23) % 200,
                     20 + (x * 31 + y * 11) % 200));
        }
    }
    return image;
}

QPixmap sourcePixmap(const QImage& image, qreal devicePixelRatio)
{
    QPixmap pixmap = QPixmap::fromImage(image);
    pixmap.setDevicePixelRatio(devicePixelRatio);
    return pixmap;
}

QImage editorPreview(const QPixmap& source,
                     const CaptureSourceMapping& mapping)
{
    constexpr int PreviewDevicePixelRatio = 5;
    QImage preview(mapping.editorBounds().size() * PreviewDevicePixelRatio,
                   QImage::Format_ARGB32_Premultiplied);
    preview.setDevicePixelRatio(PreviewDevicePixelRatio);
    preview.fill(Qt::transparent);
    QPainter painter(&preview);
    painter.drawPixmap(mapping.contentOrigin(), source);
    return preview;
}

bool isInside(const QRect& rect, int x, int y)
{
    return rect.contains(QPoint(x, y));
}

QImage renderSecurePixelate(const QImage& input,
                            const CaptureSourceMapping& mapping,
                            const QRect& logicalSelection,
                            int toolSize)
{
    const QRect sourceSelection = mapping.pixelRect(logicalSelection);
    const QRectF destination =
      mapping.editorRectForSourcePixelRect(sourceSelection);
    const QPixmap source = sourcePixmap(input, mapping.scale());
    QPixmap output = source;
    QObject effectParent;
    QPainter painter(&output);
    painter.translate(mapping.commitTranslation());
    PixelateRenderer::render(painter,
                             source,
                             logicalSelection,
                             sourceSelection,
                             destination,
                             toolSize,
                             false,
                             &effectParent);
    painter.end();
    return output.toImage();
}

} // namespace

class SourceSamplingToolsTest : public QObject
{
    Q_OBJECT

private slots:
    void invertOverwritesEveryOutwardSourcePixel()
    {
        constexpr qreal Scale = 1.25;
        const CaptureSourceMapping mapping(
          QRect(3, 5, 101, 81), Scale, QSize(127, 102));
        const QRect logicalSelection(2, 2, 4, 4);
        const QRect sourceSelection = mapping.pixelRect(logicalSelection);
        QCOMPARE(sourceSelection, QRect(3, 2, 6, 6));

        const QImage input = patternedImage(mapping.sourcePixelBounds().size());
        const QPixmap source = sourcePixmap(input, Scale);
        QPixmap output = source;

        InvertTool tool;
        // InvertTool's two QPoint QRect is inclusive. Establish a 4x4 logical
        // selection without constructing a CaptureContext.
        tool.drawMove(QPoint(logicalSelection.width() - 1,
                             logicalSelection.height() - 1));
        tool.move(logicalSelection.topLeft());
        QCOMPARE(tool.boundingRect(), logicalSelection);
        tool.setSourceMapping(mapping);

        {
            QPainter painter(&output);
            painter.translate(mapping.commitTranslation());
            tool.process(painter, source);
        }

        const QImage rendered = output.toImage();
        for (int y = 0; y < input.height(); ++y) {
            for (int x = 0; x < input.width(); ++x) {
                const QColor before = input.pixelColor(x, y);
                const QColor expected = isInside(sourceSelection, x, y)
                  ? QColor(255 - before.red(),
                           255 - before.green(),
                           255 - before.blue(),
                           before.alpha())
                  : before;
                QCOMPARE(rendered.pixelColor(x, y), expected);
            }
        }

        QImage livePreview = editorPreview(source, mapping);
        {
            QPainter painter(&livePreview);
            tool.process(painter, source);
        }
        QCOMPARE(livePreview, editorPreview(output, mapping));
    }

    void securePixelateOverwritesEveryOutwardSourcePixel()
    {
        constexpr qreal Scale = 1.25;
        constexpr int ToolSize = 50;
        const CaptureSourceMapping mapping(
          QRect(3, 5, 101, 81), Scale, QSize(127, 102));
        // A one-logical-pixel selection still owns multiple outward source
        // pixels. It also exercises Pixelate's minimum 1x1 effect buffer.
        const QRect logicalSelection(2, 2, 1, 1);
        const QRect sourceSelection = mapping.pixelRect(logicalSelection);
        QCOMPARE(sourceSelection, QRect(3, 2, 2, 2));
        const QRectF destination =
          mapping.editorRectForSourcePixelRect(sourceSelection);

        const QColor fringeColor(20, 180, 40);
        const QColor secretColor(230, 15, 210);
        QImage input(mapping.sourcePixelBounds().size(),
                     QImage::Format_ARGB32_Premultiplied);
        input.fill(fringeColor);
        for (int y = sourceSelection.top(); y <= sourceSelection.bottom(); ++y) {
            for (int x = sourceSelection.left(); x <= sourceSelection.right();
                 ++x) {
                input.setPixelColor(x, y, secretColor);
            }
        }

        const QPixmap source = sourcePixmap(input, Scale);
        QPixmap output = source;
        QObject effectParent;
        {
            QPainter painter(&output);
            painter.translate(mapping.commitTranslation());
            PixelateRenderer::render(painter,
                                     source,
                                     logicalSelection,
                                     sourceSelection,
                                     destination,
                                     ToolSize,
                                     false,
                                     &effectParent);
        }

        const QImage rendered = output.toImage();
        for (int y = 0; y < input.height(); ++y) {
            for (int x = 0; x < input.width(); ++x) {
                if (isInside(sourceSelection, x, y)) {
                    QVERIFY2(rendered.pixelColor(x, y) != secretColor,
                             "secure pixelate left an exported source pixel "
                             "unchanged");
                } else {
                    QCOMPARE(rendered.pixelColor(x, y), fringeColor);
                }
            }
        }

        QImage livePreview = editorPreview(source, mapping);
        {
            QPainter painter(&livePreview);
            PixelateRenderer::render(painter,
                                     source,
                                     logicalSelection,
                                     sourceSelection,
                                     destination,
                                     ToolSize,
                                     false,
                                     &effectParent);
        }
        QCOMPARE(livePreview, editorPreview(output, mapping));
    }

    void securePixelateNeverSamplesProtectedPixels_data()
    {
        QTest::addColumn<QRect>("logicalSelection");

        QTest::newRow("top-edge") << QRect(10, 0, 4, 4);
        QTest::newRow("left-edge") << QRect(0, 10, 4, 4);
        QTest::newRow("top-left-edge") << QRect(0, 0, 4, 4);
        QTest::newRow("full-source") << QRect(0, 0, 101, 81);
    }

    void securePixelateNeverSamplesProtectedPixels()
    {
        QFETCH(QRect, logicalSelection);

        constexpr int ToolSize = 50;
        const CaptureSourceMapping mapping(
          QRect(3, 5, 101, 81), 1.25, QSize(127, 102));
        const QRect sourceSelection = mapping.pixelRect(logicalSelection);

        const QColor exteriorColor(20, 180, 40);
        const QColor firstSecret(230, 15, 210);
        const QColor secondSecret(15, 40, 235);
        QImage first(mapping.sourcePixelBounds().size(),
                     QImage::Format_ARGB32_Premultiplied);
        QImage second(first.size(), first.format());
        first.fill(exteriorColor);
        second.fill(exteriorColor);
        for (int y = sourceSelection.top(); y <= sourceSelection.bottom(); ++y) {
            for (int x = sourceSelection.left(); x <= sourceSelection.right();
                 ++x) {
                first.setPixelColor(x, y, firstSecret);
                second.setPixelColor(x, y, secondSecret);
            }
        }

        const QImage firstOutput = renderSecurePixelate(
          first, mapping, logicalSelection, ToolSize);
        const QImage secondOutput = renderSecurePixelate(
          second, mapping, logicalSelection, ToolSize);

        // Identical exterior pixels must produce byte-identical secure output
        // regardless of every protected input pixel. For a full-source
        // selection, this proves the deterministic no-input fallback.
        QCOMPARE(firstOutput, secondOutput);
    }
};

QTEST_MAIN(SourceSamplingToolsTest)

#include "source_sampling_tools_test.moc"
