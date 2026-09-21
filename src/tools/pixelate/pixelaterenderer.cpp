// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2017-2019 Alejandro Sirgo Rica & Contributors
// SPDX-FileCopyrightText: 2026 Spencer Norton

#include "pixelaterenderer.h"

#include <QColor>
#include <QGraphicsBlurEffect>
#include <QGraphicsPixmapItem>
#include <QGraphicsScene>
#include <QImage>
#include <QPainter>
#include <QPixmap>

#include <algorithm>
#include <array>
#include <random>

namespace PixelateRenderer
{

void render(QPainter& painter,
            const QPixmap& pixmap,
            const QRect& logicalSelection,
            const QRect& sourcePixelSelection,
            const QRectF& destination,
            int toolSize,
            bool insecurePixelate,
            QObject* effectParent)
{
    if (logicalSelection.isEmpty() || sourcePixelSelection.isEmpty()
        || destination.isEmpty()) {
        return;
    }
    const QRect sourceBounds(QPoint(0, 0), pixmap.size());
    const auto width = qMax(
      1,
      static_cast<int>(logicalSelection.width()
                       * (0.5 / qMax(1, toolSize + 1))));
    const auto height = qMax(
      1,
      static_cast<int>(logicalSelection.height()
                       * (0.5 / qMax(1, toolSize + 1))));
    const auto effectSize = QSize(width, height);

    if (insecurePixelate) {
        if (toolSize <= 1) {
            auto* blur = new QGraphicsBlurEffect(effectParent);
            blur->setBlurRadius(10);
            auto* item =
              new QGraphicsPixmapItem(pixmap.copy(sourcePixelSelection));
            item->setGraphicsEffect(blur);

            QGraphicsScene scene;
            scene.addItem(item);

            scene.render(&painter, destination, QRectF());
            blur->setBlurRadius(12);
            // Multiple repeats make the blur effect stronger.
            scene.render(&painter, destination, QRectF());
        } else {
            auto pixmapPixelated = pixmap.copy(sourcePixelSelection);
            pixmapPixelated = pixmapPixelated.scaled(
              effectSize, Qt::IgnoreAspectRatio, Qt::SmoothTransformation);
            pixmapPixelated = pixmapPixelated.scaled(logicalSelection.width(),
                                                     logicalSelection.height());
            painter.drawImage(destination, pixmapPixelated.toImage());
        }
        return;
    }

    // The PRNG is only used for visual effects and is not part of the security
    // boundary.
    std::mt19937 prng(42);

    // Noise for the sampling process avoids only sampling from a small subset
    // of the fringe.
    std::normal_distribution<float> samplingNoise(0, 5 * toolSize + 1);

    // Additional noise avoids a monochromatic box when the fringe itself is
    // monochromatic.
    std::normal_distribution<float> noise(0, 0.1f);

    // Only pixels strictly outside the protected selection may influence the
    // secure effect. A missing edge must never fall back to the selection's
    // own boundary pixels.
    std::array<QImage, 4> fringe;
    if (sourcePixelSelection.top() > sourceBounds.top()) {
        fringe[0] = pixmap
                      .copy(QRect(sourcePixelSelection.left(),
                                  sourcePixelSelection.top() - 1,
                                  sourcePixelSelection.width(),
                                  1))
                      .toImage();
    }
    if (sourcePixelSelection.bottom() < sourceBounds.bottom()) {
        fringe[1] = pixmap
                      .copy(QRect(sourcePixelSelection.left(),
                                  sourcePixelSelection.bottom() + 1,
                                  sourcePixelSelection.width(),
                                  1))
                      .toImage();
    }
    if (sourcePixelSelection.left() > sourceBounds.left()) {
        fringe[2] = pixmap
                      .copy(QRect(sourcePixelSelection.left() - 1,
                                  sourcePixelSelection.top(),
                                  1,
                                  sourcePixelSelection.height()))
                      .toImage();
    }
    if (sourcePixelSelection.right() < sourceBounds.right()) {
        fringe[3] = pixmap
                      .copy(QRect(sourcePixelSelection.right() + 1,
                                  sourcePixelSelection.top(),
                                  1,
                                  sourcePixelSelection.height()))
                      .toImage();
    }

    QImage pixelated(effectSize, QImage::Format_RGB32);
    const auto availableFringe = std::find_if(
      fringe.cbegin(), fringe.cend(), [](const QImage& image) {
          return !image.isNull();
      });
    if (availableFringe == fringe.cend()) {
        // A full-source selection has no safe sample input. Use a neutral,
        // deterministic fill that is independent of all protected pixels.
        pixelated.fill(qRgb(0x80, 0x80, 0x80));
    } else {
        for (QImage& image : fringe) {
            if (image.isNull()) {
                image = *availableFringe;
            }
        }

        std::array<std::array<float, 3>, 4> samples;
        for (int x = 0; x < width; ++x) {
            for (int y = 0; y < height; ++y) {
                const float n = noise(prng);
                const float horizontal = x / static_cast<float>(width);
                const float vertical = y / static_cast<float>(height);

                for (int i = 0; i < 4; ++i) {
                    const QColor c = fringe[i].pixel(
                      std::clamp(
                        static_cast<int>(horizontal * fringe[i].width()
                                         + samplingNoise(prng)),
                        0,
                        fringe[i].width() - 1),
                      std::clamp(
                        static_cast<int>(vertical * fringe[i].height()
                                         + samplingNoise(prng)),
                        0,
                        fringe[i].height() - 1));
                    samples[i][0] = c.redF();
                    samples[i][1] = c.greenF();
                    samples[i][2] = c.blueF();
                }

                const float weightH =
                  (qMin(x, width - x) / static_cast<float>(width))
                  - (qMin(y, height - y) / static_cast<float>(height)) + 0.5f;
                const float weightV = 1.0f - weightH;

                std::array<int, 3> rgb = { 0, 0, 0 };
                for (int i = 0; i < 3; ++i) {
                    const float c =
                      weightH * ((1 - horizontal) * samples[2][i]
                                 + horizontal * samples[3][i])
                      + weightV * ((1 - vertical) * samples[0][i]
                                   + vertical * samples[1][i])
                      + n;

                    rgb[i] =
                      std::clamp(static_cast<int>(0xff * c), 0, 0xff);
                }
                pixelated.setPixel(x, y, qRgb(rgb[0], rgb[1], rgb[2]));
            }
        }
    }

    pixelated = pixelated.scaled(logicalSelection.width(),
                                 logicalSelection.height(),
                                 Qt::IgnoreAspectRatio,
                                 Qt::FastTransformation);
    painter.drawImage(destination, pixelated);
}

} // namespace PixelateRenderer
