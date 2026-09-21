// SPDX-License-Identifier: GPL-3.0-or-later

function finiteNumber(value, fallback = 0) {
    return Number.isFinite(value) ? value : fallback;
}

export function clamp(value, minimum, maximum) {
    return Math.min(Math.max(value, minimum), maximum);
}

export function normalizeBounds(bounds) {
    return {
        width: Math.max(1, Math.floor(finiteNumber(bounds?.width, 1))),
        height: Math.max(1, Math.floor(finiteNumber(bounds?.height, 1))),
    };
}

/**
 * Convert two stage-coordinate points to an inclusive logical-pixel rectangle.
 * The result is always clamped to the stage and is at least 1x1.
 */
export function selectionFromPoints(start, end, bounds) {
    const normalizedBounds = normalizeBounds(bounds);
    const maximumX = normalizedBounds.width - 1;
    const maximumY = normalizedBounds.height - 1;
    const startX = clamp(Math.floor(finiteNumber(start?.x)), 0, maximumX);
    const startY = clamp(Math.floor(finiteNumber(start?.y)), 0, maximumY);
    const endX = clamp(Math.floor(finiteNumber(end?.x)), 0, maximumX);
    const endY = clamp(Math.floor(finiteNumber(end?.y)), 0, maximumY);
    const x = Math.min(startX, endX);
    const y = Math.min(startY, endY);

    return {
        x,
        y,
        width: Math.max(startX, endX) - x + 1,
        height: Math.max(startY, endY) - y + 1,
    };
}

/**
 * Return four non-overlapping rectangles which dim everything outside a
 * selection. This avoids a compositor readback or a custom Clutter shader.
 */
export function shadeRectangles(selection, bounds) {
    const normalizedBounds = normalizeBounds(bounds);
    const x = clamp(Math.floor(finiteNumber(selection?.x)), 0,
        normalizedBounds.width - 1);
    const y = clamp(Math.floor(finiteNumber(selection?.y)), 0,
        normalizedBounds.height - 1);
    const width = clamp(Math.floor(finiteNumber(selection?.width, 1)), 1,
        normalizedBounds.width - x);
    const height = clamp(Math.floor(finiteNumber(selection?.height, 1)), 1,
        normalizedBounds.height - y);

    return {
        top: {x: 0, y: 0, width: normalizedBounds.width, height: y},
        bottom: {
            x: 0,
            y: y + height,
            width: normalizedBounds.width,
            height: normalizedBounds.height - y - height,
        },
        left: {x: 0, y, width: x, height},
        right: {
            x: x + width,
            y,
            width: normalizedBounds.width - x - width,
            height,
        },
    };
}

export function labelPosition(selection, labelSize, bounds, padding = 12) {
    const normalizedBounds = normalizeBounds(bounds);
    const labelWidth = Math.max(1,
        Math.ceil(finiteNumber(labelSize?.width, 1)));
    const labelHeight = Math.max(1,
        Math.ceil(finiteNumber(labelSize?.height, 1)));
    const maximumX = Math.max(0, normalizedBounds.width - labelWidth - padding);
    const maximumY = Math.max(0, normalizedBounds.height - labelHeight - padding);

    return {
        x: clamp(selection.x + selection.width + padding, padding, maximumX),
        y: clamp(selection.y + selection.height + padding, padding, maximumY),
    };
}

export function formatDimensions(selection) {
    return `${selection.width} × ${selection.height}`;
}
