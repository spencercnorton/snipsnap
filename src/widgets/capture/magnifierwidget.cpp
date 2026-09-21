// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2017-2019 Alejandro Sirgo Rica & Contributors

#include "magnifierwidget.h"

#include <QApplication>
#include <QEvent>
#include <QMouseEvent>
#include <QPainter>
#include <QPainterPath>
#include <QPen>
#include <QPixmap>

#include <cmath>

MagnifierWidget::MagnifierWidget(const QPixmap& p,
                                 const QColor& c,
                                 bool isSquare,
                                 QWidget* parent,
                                 const CaptureSourceMapping& sourceMapping)
  : QWidget(parent)
  , m_square(isSquare)
  , m_color(c)
  , m_borderColor(c)
  , m_screenshot(p)
  , m_sourceMapping(sourceMapping)
{
    setFixedSize(parent->width(), parent->height());
    setAttribute(Qt::WA_TransparentForMouseEvents);
    m_color.setAlpha(130);
    if (m_sourceMapping.isValid()) {
        // PixmapFragment source rectangles use raw image pixels. Do not let the
        // compositor scale metadata make the magnifier resample them again.
        m_screenshot.setDevicePixelRatio(1.0);
    }
    // add padding for circular magnifier
    QImage padded(m_screenshot.width() + 2 * m_magPixels,
                  m_screenshot.height() + 2 * m_magPixels,
                  QImage::Format_ARGB32);
    padded.fill(Qt::black);
    QPainter painter(&padded);
    painter.drawPixmap(m_magPixels, m_magPixels, m_screenshot);
    m_paddedScreenshot.convertFromImage(padded);
}
void MagnifierWidget::paintEvent(QPaintEvent*)
{
    QPainter p(this);
    if (!p.isActive()) {
        return;
    }
    if (m_square) {
        drawMagnifier(p);
    } else {
        drawMagnifierCircle(p);
    }
}

void MagnifierWidget::drawMagnifierCircle(QPainter& painter)
{
    auto relativeCursor = QCursor::pos();
    auto translated = QWidget::mapFromGlobal(relativeCursor);
    auto x = translated.x() + m_magPixels;
    auto y = translated.y() + m_magPixels;

    int magX;
    int magY;
    if (m_sourceMapping.isValid()) {
        const QRect sourceRect = sourceRectForEditorPoint(
          m_sourceMapping, translated, m_magPixels, true);
        magX = sourceRect.x();
        magY = sourceRect.y();
    } else {
        // Preserve the legacy coordinate contract for non-bridge captures.
        magX = x - m_magPixels;
        magY = y - m_magPixels;
    }
    QRectF magniRect(magX, magY, m_pixels, m_pixels);

    qreal drawPosX = x + m_magOffset + m_pixels * magZoom / 2;
    if (drawPosX > width() - m_pixels * magZoom / 2) {
        drawPosX = x - m_magOffset - m_pixels * magZoom / 2;
    }
    qreal drawPosY = y + m_magOffset + m_pixels * magZoom / 2;
    if (drawPosY > height() - m_pixels * magZoom / 2) {
        drawPosY = y - m_magOffset - m_pixels * magZoom / 2;
    }
    QPointF drawPos(drawPosX, drawPosY);
    QRectF crossHairTop(drawPos.x() + magZoom * (-0.5),
                        drawPos.y() - magZoom * (m_magPixels + 0.5),
                        magZoom,
                        magZoom * (m_magPixels));
    QRectF crossHairRight(drawPos.x() + magZoom * (0.5),
                          drawPos.y() + magZoom * (-0.5),
                          magZoom * (m_magPixels),
                          magZoom);
    QRectF crossHairBottom(drawPos.x() + magZoom * (-0.5),
                           drawPos.y() + magZoom * (0.5),
                           magZoom,
                           magZoom * (m_magPixels));
    QRectF crossHairLeft(drawPos.x() - magZoom * (m_magPixels + 0.5),
                         drawPos.y() + magZoom * (-0.5),
                         magZoom * (m_magPixels),
                         magZoom);
    const auto frag =
      QPainter::PixmapFragment::create(drawPos, magniRect, magZoom, magZoom);

    painter.setRenderHint(QPainter::Antialiasing, true);
    QPainterPath path = QPainterPath();
    path.addEllipse(drawPos, m_pixels * magZoom / 2, m_pixels * magZoom / 2);
    painter.setClipPath(path);

    painter.drawPixmapFragments(
      &frag, 1, m_paddedScreenshot, QPainter::OpaqueHint);
    painter.setCompositionMode(QPainter::CompositionMode_SourceOver);
    for (const auto& rect :
         { crossHairTop, crossHairRight, crossHairBottom, crossHairLeft }) {
        painter.fillRect(rect, m_color);
    }
    QPen pen(m_borderColor);
    pen.setWidth(4);
    painter.setPen(pen);
    painter.drawEllipse(
      drawPos, m_pixels * magZoom / 2, m_pixels * magZoom / 2);
}
// https://invent.kde.org/graphics/spectacle/-/blob/master/src/QuickEditor/QuickEditor.cpp#L841
void MagnifierWidget::drawMagnifier(QPainter& painter)
{
    auto relativeCursor = QCursor::pos();
    auto translated = QWidget::mapFromGlobal(relativeCursor);
    auto x = translated.x();
    auto y = translated.y();

    const QRect mappedSourceRect = sourceRectForEditorPoint(
      m_sourceMapping, translated, m_magPixels, false);
    int magX = mappedSourceRect.x();
    int offsetX = 0;
    if (magX < 0) {
        offsetX = magX;
        magX = 0;
    } else {
        const int maxX = m_screenshot.width() - m_pixels;
        if (magX > maxX) {
            offsetX = magX - maxX;
            magX = maxX;
        }
    }
    int magY = mappedSourceRect.y();
    int offsetY = 0;
    if (magY < 0) {
        offsetY = magY;
        magY = 0;
    } else {
        const int maxY = m_screenshot.height() - m_pixels;
        if (magY > maxY) {
            offsetY = magY - maxY;
            magY = maxY;
        }
    }
    QRectF magniRect(magX, magY, m_pixels, m_pixels);

    qreal drawPosX = x + m_magOffset + m_pixels * magZoom / 2;
    if (drawPosX > width() - m_pixels * magZoom / 2) {
        drawPosX = x - m_magOffset - m_pixels * magZoom / 2;
    }
    qreal drawPosY = y + m_magOffset + m_pixels * magZoom / 2;
    if (drawPosY > height() - m_pixels * magZoom / 2) {
        drawPosY = y - m_magOffset - m_pixels * magZoom / 2;
    }
    QPointF drawPos(drawPosX, drawPosY);
    QRectF crossHairTop(drawPos.x() + magZoom * (offsetX - 0.5),
                        drawPos.y() - magZoom * (m_magPixels + 0.5),
                        magZoom,
                        magZoom * (m_magPixels + offsetY));
    QRectF crossHairRight(drawPos.x() + magZoom * (0.5 + offsetX),
                          drawPos.y() + magZoom * (offsetY - 0.5),
                          magZoom * (m_magPixels - offsetX),
                          magZoom);
    QRectF crossHairBottom(drawPos.x() + magZoom * (offsetX - 0.5),
                           drawPos.y() + magZoom * (0.5 + offsetY),
                           magZoom,
                           magZoom * (m_magPixels - offsetY));
    QRectF crossHairLeft(drawPos.x() - magZoom * (m_magPixels + 0.5),
                         drawPos.y() + magZoom * (offsetY - 0.5),
                         magZoom * (m_magPixels + offsetX),
                         magZoom);
    QRectF crossHairBorder(drawPos.x() - magZoom * (m_magPixels + 0.5) - 1,
                           drawPos.y() - magZoom * (m_magPixels + 0.5) - 1,
                           m_pixels * magZoom + 2,
                           m_pixels * magZoom + 2);
    const auto frag =
      QPainter::PixmapFragment::create(drawPos, magniRect, magZoom, magZoom);

    painter.fillRect(crossHairBorder, m_borderColor);
    painter.drawPixmapFragments(&frag, 1, m_screenshot, QPainter::OpaqueHint);
    painter.setCompositionMode(QPainter::CompositionMode_SourceOver);
    for (const auto& rect :
         { crossHairTop, crossHairRight, crossHairBottom, crossHairLeft }) {
        painter.fillRect(rect, m_color);
    }
}
