// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#include "regioneditorviewport.h"

#include <QtMath>

#include <algorithm>

qreal regionEditorFitScale(const QSize& canvasSize,
                           const QSize& availableSize)
{
    if (canvasSize.isEmpty() || availableSize.isEmpty()) {
        return 0.0;
    }

    return std::min(
      { qreal{ 1.0 },
        static_cast<qreal>(availableSize.width()) / canvasSize.width(),
        static_cast<qreal>(availableSize.height()) / canvasSize.height() });
}

QPointF regionEditorAnchoredCenter(const QPointF& centerAfterTransform,
                                   const QPointF& anchorBeforeTransform,
                                   const QPointF& anchorAfterTransform)
{
    return centerAfterTransform + anchorBeforeTransform - anchorAfterTransform;
}

QSize regionEditorViewportSize(const QSize& canvasSize,
                               const QSize& availableSize)
{
    const qreal scale = regionEditorFitScale(canvasSize, availableSize);
    if (scale <= 0.0) {
        return {};
    }
    return { std::max(1, qFloor(canvasSize.width() * scale)),
             std::max(1, qFloor(canvasSize.height() * scale)) };
}
