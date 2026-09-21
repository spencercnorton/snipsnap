// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#pragma once

#include <QPointF>
#include <QRect>
#include <QRectF>
#include <QSize>

#include <cmath>

/**
 * Maps editor-local logical coordinates to a compositor-provided pixel image.
 *
 * A compositor crop is outward-rounded. If logicalOrigin * scale is
 * fractional, source pixel (0, 0) starts before the editor's logical origin.
 * Keeping that leading phase explicit gives every source-image consumer the
 * same coordinate contract without changing CaptureTool::process()'s
 * signature.
 */
class CaptureSourceMapping
{
public:
    CaptureSourceMapping() = default;

    CaptureSourceMapping(const QRect& logicalSourceRect,
                         qreal scale,
                         const QSize& sourcePixelSize)
      : m_logicalSourceRect(logicalSourceRect)
      , m_scale(scale)
      , m_sourcePixelSize(sourcePixelSize)
      , m_valid(!logicalSourceRect.isEmpty() && scale > 0.0
                && std::isfinite(scale) && !sourcePixelSize.isEmpty())
    {
        if (!m_valid) {
            return;
        }
        m_scaledLogicalOrigin = {
            snapNearInteger(logicalSourceRect.x() * scale),
            snapNearInteger(logicalSourceRect.y() * scale),
        };
        m_leadingPixelPhase = {
            fractionalPart(m_scaledLogicalOrigin.x()),
            fractionalPart(m_scaledLogicalOrigin.y()),
        };
    }

    bool isValid() const
    {
        return m_valid;
    }

    qreal scale() const
    {
        return m_scale;
    }

    QRect editorBounds() const
    {
        return { QPoint(0, 0), m_logicalSourceRect.size() };
    }

    QRect sourcePixelBounds() const
    {
        return { QPoint(0, 0), m_sourcePixelSize };
    }

    QPointF leadingPixelPhase() const
    {
        return m_leadingPixelPhase;
    }

    QPointF sourcePixelF(const QPointF& editorLogicalPoint) const
    {
        return { m_leadingPixelPhase.x() + editorLogicalPoint.x() * m_scale,
                 m_leadingPixelPhase.y() + editorLogicalPoint.y() * m_scale };
    }

    QPoint sourcePixel(const QPointF& editorLogicalPoint) const
    {
        const QPointF source = sourcePixelF(editorLogicalPoint);
        return { static_cast<int>(std::floor(source.x())),
                 static_cast<int>(std::floor(source.y())) };
    }

    QPointF editorPointForSourcePixel(const QPointF& sourcePixelPoint) const
    {
        return { (sourcePixelPoint.x() - m_leadingPixelPhase.x()) / m_scale,
                 (sourcePixelPoint.y() - m_leadingPixelPhase.y()) / m_scale };
    }

    /**
     * Return the editor-space destination whose edges map exactly to the
     * half-open boundaries of sourcePixelRect.
     *
     * Source-sampling tools copy an outward-rounded raw rectangle. Drawing
     * that copy back into the original integer logical selection would scale
     * or clip its fractional edge pixels. This rectangle deliberately extends
     * to those raw pixel boundaries so every copied pixel is overwritten.
     */
    QRectF editorRectForSourcePixelRect(const QRect& sourcePixelRect) const
    {
        if (!m_valid) {
            return {};
        }
        const QRect source = sourcePixelRect.normalized().intersected(
          sourcePixelBounds());
        if (source.isEmpty()) {
            return {};
        }
        const QPointF topLeft = editorPointForSourcePixel(source.topLeft());
        const QPointF bottomRight = editorPointForSourcePixel(
          QPointF(source.x() + source.width(), source.y() + source.height()));
        return QRectF(topLeft, bottomRight);
    }

    QPointF absolutePixelF(const QPointF& editorLogicalPoint) const
    {
        return { m_scaledLogicalOrigin.x() + editorLogicalPoint.x() * m_scale,
                 m_scaledLogicalOrigin.y() + editorLogicalPoint.y() * m_scale };
    }

    QPointF editorPointForAbsolutePixel(
      const QPointF& absolutePixelPoint) const
    {
        return { (absolutePixelPoint.x() - m_scaledLogicalOrigin.x()) / m_scale,
                 (absolutePixelPoint.y() - m_scaledLogicalOrigin.y()) / m_scale };
    }

    QPoint snapEditorPointToPixelGrid(const QPoint& editorLogicalPoint,
                                      int gridSize) const
    {
        if (!m_valid || gridSize <= 0) {
            return editorLogicalPoint;
        }
        const QPointF absolute = absolutePixelF(editorLogicalPoint);
        const QPointF absoluteEnd(
          m_scaledLogicalOrigin.x() + m_logicalSourceRect.width() * m_scale,
          m_scaledLogicalOrigin.y() + m_logicalSourceRect.height() * m_scale);
        const auto snapAxis = [gridSize](qreal value,
                                         qreal start,
                                         qreal end) {
            const qreal first = std::ceil(start / gridSize) * gridSize;
            const qreal last =
              std::floor(std::nextafter(end, start) / gridSize) * gridSize;
            if (first > last) {
                return value;
            }
            return qBound(first,
                          std::round(value / gridSize) * gridSize,
                          last);
        };
        const QPointF snapped(
          snapAxis(absolute.x(), m_scaledLogicalOrigin.x(), absoluteEnd.x()),
          snapAxis(absolute.y(), m_scaledLogicalOrigin.y(), absoluteEnd.y()));
        const QPointF editor = editorPointForAbsolutePixel(snapped);
        return { qRound(editor.x()), qRound(editor.y()) };
    }

    // Place source pixel (0, 0) at its actual position before logical origin.
    QPointF contentOrigin() const
    {
        return { -m_leadingPixelPhase.x() / m_scale,
                 -m_leadingPixelPhase.y() / m_scale };
    }

    // Map editor-local tool coordinates into the outward pixel crop.
    QPointF commitTranslation() const
    {
        return { m_leadingPixelPhase.x() / m_scale,
                 m_leadingPixelPhase.y() / m_scale };
    }

    QRect pixelRect(const QRect& editorLogicalRect) const
    {
        if (!m_valid) {
            return {};
        }
        const QRect bounds = editorBounds();
        const QRect logical = editorLogicalRect.normalized().intersected(bounds);
        if (logical.isEmpty()) {
            return {};
        }

        int left = static_cast<int>(std::floor(
          m_leadingPixelPhase.x() + logical.x() * m_scale));
        int top = static_cast<int>(std::floor(
          m_leadingPixelPhase.y() + logical.y() * m_scale));
        int right = static_cast<int>(std::ceil(
          m_leadingPixelPhase.x()
          + (logical.x() + logical.width()) * m_scale));
        int bottom = static_cast<int>(std::ceil(
          m_leadingPixelPhase.y()
          + (logical.y() + logical.height()) * m_scale));

        // Protocol v1 permits one pixel of compositor size tolerance. A
        // selection touching an editor edge owns the corresponding source edge.
        if (logical.left() == bounds.left()) {
            left = 0;
        }
        if (logical.top() == bounds.top()) {
            top = 0;
        }
        if (logical.x() + logical.width() == bounds.width()) {
            right = m_sourcePixelSize.width();
        }
        if (logical.y() + logical.height() == bounds.height()) {
            bottom = m_sourcePixelSize.height();
        }
        return QRect(QPoint(left, top), QSize(right - left, bottom - top))
          .intersected(sourcePixelBounds());
    }

    QRect logicalExportRect(const QRect& editorLogicalRect) const
    {
        if (!m_valid) {
            return {};
        }
        const QRect logical = editorLogicalRect.normalized().intersected(
          editorBounds());
        if (logical.isEmpty()) {
            return {};
        }
        return { m_logicalSourceRect.topLeft() + logical.topLeft(),
                 logical.size() };
    }

private:
    static qreal snapNearInteger(qreal value)
    {
        constexpr qreal Epsilon = 1e-9;
        const qreal nearest = std::round(value);
        return std::abs(value - nearest) <= Epsilon ? nearest : value;
    }

    static qreal fractionalPart(qreal value)
    {
        const qreal fraction = value - std::floor(value);
        constexpr qreal Epsilon = 1e-9;
        return fraction <= Epsilon || 1.0 - fraction <= Epsilon ? 0.0
                                                                : fraction;
    }

    QRect m_logicalSourceRect;
    qreal m_scale{ 1.0 };
    QSize m_sourcePixelSize;
    QPointF m_scaledLogicalOrigin;
    QPointF m_leadingPixelPhase;
    bool m_valid{ false };
};
