// SPDX-License-Identifier: GPL-3.0-or-later

import {
    MAX_DIMENSION,
    MAX_PIXEL_COUNT,
} from './handoff-protocol.js';

function requireFinite(value, name) {
    if (!Number.isFinite(value))
        throw new TypeError(`${name} must be finite`);
    return value;
}

function requirePositiveInteger(value, maximum, name) {
    if (!Number.isInteger(value) || value < 1 || value > maximum)
        throw new RangeError(`${name} is outside handoff bounds`);
    return value;
}

function greatestCommonDivisor(left, right) {
    while (right !== 0) {
        [left, right] = [right, left % right];
    }
    return left;
}

const GNOME_SCALE_MAX_DENOMINATOR = 4;
// Clutter exposes the scale through a C float, so allow its float32 rounding
// error while remaining far below the spacing between denominator<=4 values.
const GNOME_SCALE_EPSILON = 1e-6;

/**
 * Convert a Shell stage selection to a texture-pixel rectangle.
 *
 * Outward rounding is intentional: a fractional-scale selection must never
 * omit a logical edge pixel. A rectangle outside the immutable captured stage
 * fails closed instead of silently changing the user's selection.
 */
export function selectionToPixelRegion(
    selection, scale, textureSize, logicalBounds) {
    const logicalX = Math.floor(requireFinite(selection?.x, 'selection.x'));
    const logicalY = Math.floor(requireFinite(selection?.y, 'selection.y'));
    const logicalWidth = requirePositiveInteger(
        selection?.width, MAX_DIMENSION, 'selection.width');
    const logicalHeight = requirePositiveInteger(
        selection?.height, MAX_DIMENSION, 'selection.height');
    const textureWidth = requirePositiveInteger(
        textureSize?.width, 0x7fffffff, 'texture.width');
    const textureHeight = requirePositiveInteger(
        textureSize?.height, 0x7fffffff, 'texture.height');
    const logicalBoundWidth = requirePositiveInteger(
        logicalBounds?.width, 0x7fffffff, 'logicalBounds.width');
    const logicalBoundHeight = requirePositiveInteger(
        logicalBounds?.height, 0x7fffffff, 'logicalBounds.height');

    const logicalEndX = logicalX + logicalWidth;
    const logicalEndY = logicalY + logicalHeight;
    if (!Number.isSafeInteger(logicalEndX) ||
        !Number.isSafeInteger(logicalEndY) ||
        logicalX < 0 || logicalY < 0 ||
        logicalEndX > logicalBoundWidth ||
        logicalEndY > logicalBoundHeight) {
        throw new RangeError('selection lies outside the captured logical stage');
    }

    const scaleFraction = scaleToFraction(scale);
    const scaledFloor = value => Math.floor(
        value * scaleFraction.numerator / scaleFraction.denominator);
    const scaledCeil = value => Math.ceil(
        value * scaleFraction.numerator / scaleFraction.denominator);

    const rawStartX = scaledFloor(logicalX);
    const rawStartY = scaledFloor(logicalY);
    const rawEndX = logicalEndX === logicalBoundWidth
        ? textureWidth
        : scaledCeil(logicalEndX);
    const rawEndY = logicalEndY === logicalBoundHeight
        ? textureHeight
        : scaledCeil(logicalEndY);
    if (rawStartX < 0 || rawStartY < 0 ||
        rawEndX > textureWidth || rawEndY > textureHeight) {
        throw new RangeError('selection lies outside the captured stage texture');
    }

    const width = rawEndX - rawStartX;
    const height = rawEndY - rawStartY;

    if (width > MAX_DIMENSION || height > MAX_DIMENSION)
        throw new RangeError('selected pixel dimensions exceed handoff bounds');
    if (width * height > MAX_PIXEL_COUNT)
        throw new RangeError('selected pixel count exceeds handoff bounds');

    return {
        x: rawStartX,
        y: rawStartY,
        width,
        height,
        logicalX,
        logicalY,
        logicalWidth,
        logicalHeight,
    };
}

/**
 * Recover GNOME's canonical monitor-scale fraction for the protocol.
 *
 * Mutter 50 limits supported monitor scales to rational values with a maximum
 * denominator of four. Keeping that exact small fraction is correctness-
 * critical: a decimal approximation can turn a zero crop phase into an almost
 * whole-pixel phase once it is multiplied by the selection origin.
 */
export function scaleToFraction(scale) {
    scale = requireFinite(scale, 'scale');
    if (scale < 0.5 || scale > 4)
        throw new RangeError('scale is outside handoff bounds');

    let numerator = 0;
    let denominator = 0;
    let minimumError = Number.POSITIVE_INFINITY;
    for (let candidateDenominator = 1;
        candidateDenominator <= GNOME_SCALE_MAX_DENOMINATOR;
        candidateDenominator++) {
        const candidateNumerator = Math.round(
            scale * candidateDenominator);
        const error = Math.abs(
            scale - candidateNumerator / candidateDenominator);
        if (error < minimumError) {
            numerator = candidateNumerator;
            denominator = candidateDenominator;
            minimumError = error;
        }
    }
    if (minimumError > GNOME_SCALE_EPSILON) {
        throw new RangeError(
            'scale is not a canonical GNOME monitor fraction');
    }
    const divisor = greatestCommonDivisor(numerator, denominator);
    return {
        numerator: numerator / divisor,
        denominator: denominator / divisor,
    };
}

const SNIPSNAP_IDENTITIES = new Set([
    'snipsnap',
    'tech.norvi.snipsnap',
]);

export function editorWindowTitle(captureId) {
    let value;
    try {
        value = typeof captureId === 'bigint'
            ? captureId
            : BigInt(captureId);
    } catch (_error) {
        throw new TypeError('capture ID is invalid');
    }
    if (value <= 0n || value > 0xffffffffffffffffn)
        throw new RangeError('capture ID is outside uint64 bounds');
    return `SnipSnap [capture-id=${value}]`;
}

/**
 * Match a window observed after peer authentication. Requiring its peer PID,
 * normal type, exact capture title, and SnipSnap identity prevents activating
 * an older window, a helper window, or an unrelated same-user client.
 */
export function matchesEditorWindow(
    candidate, peerPid, normalWindowType, captureId) {
    if (!Number.isInteger(peerPid) || peerPid <= 0)
        return false;
    if (candidate?.pid !== peerPid ||
        candidate?.windowType !== normalWindowType) {
        return false;
    }
    if (candidate?.title !== editorWindowTitle(captureId))
        return false;

    const identities = Array.isArray(candidate.identities)
        ? candidate.identities
        : [];
    return identities.some(identity =>
        typeof identity === 'string' &&
        SNIPSNAP_IDENTITIES.has(identity.trim().toLowerCase()));
}

/**
 * Identity gate for asynchronous handoffs. Finishing a stale operation can
 * never clear or mutate a replacement operation.
 */
export class HandoffState {
    constructor() {
        this._activeOperation = null;
    }

    get busy() {
        return this._activeOperation !== null;
    }

    begin(request) {
        if (request === null || request === undefined || this.busy)
            return null;

        const operation = Object.freeze({request, token: Object.freeze({})});
        this._activeOperation = operation;
        return operation;
    }

    isCurrent(operation, request = operation?.request) {
        return operation !== null &&
            operation !== undefined &&
            operation === this._activeOperation &&
            operation.request === request;
    }

    finish(operation) {
        if (!this.isCurrent(operation))
            return false;

        this._activeOperation = null;
        return true;
    }

    cancel() {
        const operation = this._activeOperation;
        this._activeOperation = null;
        return operation;
    }
}

/**
 * Serialize work that the underlying compositor API cannot cancel.
 *
 * Unlike an overlay lifecycle, closing the UI must not clear this token. The
 * exact owner that observed the promise settle is the only caller allowed to
 * release it.
 */
export class UncancellableWorkGate {
    constructor() {
        this._activeOperation = null;
    }

    get busy() {
        return this._activeOperation !== null;
    }

    begin() {
        if (this.busy)
            return null;
        const operation = Object.freeze({});
        this._activeOperation = operation;
        return operation;
    }

    finish(operation) {
        if (operation === null || operation !== this._activeOperation)
            return false;
        this._activeOperation = null;
        return true;
    }
}
