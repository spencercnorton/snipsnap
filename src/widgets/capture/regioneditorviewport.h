// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#pragma once

#include <QPointF>
#include <QSize>

qreal regionEditorFitScale(const QSize& canvasSize,
                           const QSize& availableSize);
QPointF regionEditorAnchoredCenter(const QPointF& centerAfterTransform,
                                   const QPointF& anchorBeforeTransform,
                                   const QPointF& anchorAfterTransform);

/** Return the largest aspect-preserving canvas viewport that fits an output. */
QSize regionEditorViewportSize(const QSize& canvasSize,
                               const QSize& availableSize);
