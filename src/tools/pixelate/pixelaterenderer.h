// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#pragma once

#include <QRect>
#include <QRectF>

class QObject;
class QPainter;
class QPixmap;

namespace PixelateRenderer
{

/**
 * Render the production Pixelate effect into an exact source-pixel-aligned
 * destination. PixelateTool owns coordinate mapping and configuration; this
 * function owns the independently testable rendering/security boundary.
 */
void render(QPainter& painter,
            const QPixmap& pixmap,
            const QRect& logicalSelection,
            const QRect& sourcePixelSelection,
            const QRectF& destination,
            int toolSize,
            bool insecurePixelate,
            QObject* effectParent);

} // namespace PixelateRenderer
