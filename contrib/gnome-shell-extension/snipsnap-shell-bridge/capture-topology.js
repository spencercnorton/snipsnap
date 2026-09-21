// SPDX-License-Identifier: GPL-3.0-or-later

const MAX_MONITORS = 16;
const MAX_STAGE_VIEWS = 64;
const MAX_COORDINATE = 0x7fffffff;
const MIN_SCALE = 0.5;
const MAX_SCALE = 4;
const SCALE_EPSILON = 0.0001;
const MAX_CAPTURE_TEXTURE_DIMENSION = 32_768;
const MAX_CAPTURE_PIXEL_COUNT = 32_000_000;
const PHYSICAL_LAYOUT_CAPTURE_SCALE = 1;

function requireInteger(value, minimum, maximum, name) {
    if (!Number.isInteger(value) || value < minimum || value > maximum)
        throw new RangeError(`${name} is outside capture topology bounds`);
    return value;
}

function requireScale(value, name) {
    if (!Number.isFinite(value) || value < MIN_SCALE || value > MAX_SCALE)
        throw new RangeError(`${name} is outside capture topology bounds`);
    return value;
}

function rectanglesOverlap(left, right) {
    return left.x < right.x + right.width &&
        left.x + left.width > right.x &&
        left.y < right.y + right.height &&
        left.y + left.height > right.y;
}

function rectanglesAreAdjacent(left, right) {
    const verticalOverlap = Math.min(
        left.y + left.height, right.y + right.height) -
        Math.max(left.y, right.y);
    const horizontalOverlap = Math.min(
        left.x + left.width, right.x + right.width) -
        Math.max(left.x, right.x);

    return ((left.x + left.width === right.x ||
             right.x + right.width === left.x) && verticalOverlap > 0) ||
        ((left.y + left.height === right.y ||
          right.y + right.height === left.y) && horizontalOverlap > 0);
}

function requireConnected(monitors) {
    const visited = new Set([0]);
    const pending = [0];

    while (pending.length > 0) {
        const current = pending.pop();
        for (let candidate = 0; candidate < monitors.length; candidate++) {
            if (visited.has(candidate) ||
                !rectanglesAreAdjacent(monitors[current], monitors[candidate])) {
                continue;
            }
            visited.add(candidate);
            pending.push(candidate);
        }
    }

    if (visited.size !== monitors.length)
        throw new RangeError('capture topology monitors are disconnected');
}

function topologyId(signature) {
    // This compact ID is diagnostic only. Equality uses the full signature.
    let hash = 0x811c9dc5;
    for (let index = 0; index < signature.length; index++) {
        hash ^= signature.charCodeAt(index);
        hash = Math.imul(hash, 0x01000193);
    }
    return `v1-${(hash >>> 0).toString(16).padStart(8, '0')}`;
}

/**
 * Snapshot Mutter's normalized logical stage and monitor views.
 *
 * Mutter rejects negative logical positions, overlapping/disconnected views,
 * and layouts whose minimum x/y are not zero. Rotation is already represented
 * in each view's logical width/height. Keeping those invariants explicit makes
 * a compositor change fail before a stale texture can reach the editor.
 */
export function snapshotCaptureTopology(
    stageSize,
    sourceMonitors,
    topologyGeneration = 1,
    sourceStageViewScales = null) {
    const stageWidth = requireInteger(
        Math.floor(stageSize?.width), 1, MAX_COORDINATE, 'stage.width');
    const stageHeight = requireInteger(
        Math.floor(stageSize?.height), 1, MAX_COORDINATE, 'stage.height');
    if (!Array.isArray(sourceMonitors) ||
        sourceMonitors.length < 1 || sourceMonitors.length > MAX_MONITORS) {
        throw new RangeError('monitor count is outside capture topology bounds');
    }
    topologyGeneration = requireInteger(
        topologyGeneration, 1, Number.MAX_SAFE_INTEGER, 'topology generation');

    const monitors = sourceMonitors.map((monitor, position) => Object.freeze({
        index: requireInteger(
            monitor?.index, 0, MAX_MONITORS - 1, `monitor[${position}].index`),
        x: requireInteger(
            monitor?.x, 0, MAX_COORDINATE, `monitor[${position}].x`),
        y: requireInteger(
            monitor?.y, 0, MAX_COORDINATE, `monitor[${position}].y`),
        width: requireInteger(
            monitor?.width, 1, MAX_COORDINATE, `monitor[${position}].width`),
        height: requireInteger(
            monitor?.height, 1, MAX_COORDINATE, `monitor[${position}].height`),
        scale: requireScale(
            monitor?.geometry_scale, `monitor[${position}].geometry_scale`),
    })).sort((left, right) => left.index - right.index);

    const indices = new Set(monitors.map(monitor => monitor.index));
    if (indices.size !== monitors.length)
        throw new RangeError('capture topology contains duplicate monitor indices');

    // Mutter creates one StageView per CRTC, not per logical monitor. Mirrored
    // and tiled outputs may therefore contribute additional views. Every
    // logical monitor still owns at least one CRTC/view.
    if (!Array.isArray(sourceStageViewScales) ||
        sourceStageViewScales.length < monitors.length ||
        sourceStageViewScales.length > MAX_STAGE_VIEWS) {
        throw new RangeError(
            'stage view count is outside capture topology bounds');
    }
    const stageViewScales = Object.freeze(sourceStageViewScales.map(
        (scale, position) => requireScale(
            scale, `stage_view[${position}].scale`)
    ).sort((left, right) => left - right));

    let minimumX = MAX_COORDINATE;
    let minimumY = MAX_COORDINATE;
    let maximumX = 0;
    let maximumY = 0;
    for (let index = 0; index < monitors.length; index++) {
        const monitor = monitors[index];
        const right = monitor.x + monitor.width;
        const bottom = monitor.y + monitor.height;
        if (!Number.isSafeInteger(right) || !Number.isSafeInteger(bottom) ||
            right > stageWidth || bottom > stageHeight) {
            throw new RangeError('monitor lies outside the capture stage');
        }

        minimumX = Math.min(minimumX, monitor.x);
        minimumY = Math.min(minimumY, monitor.y);
        maximumX = Math.max(maximumX, right);
        maximumY = Math.max(maximumY, bottom);
        for (let previous = 0; previous < index; previous++) {
            if (rectanglesOverlap(monitor, monitors[previous]))
                throw new RangeError('capture topology monitors overlap');
        }
    }

    if (minimumX !== 0 || minimumY !== 0 ||
        maximumX !== stageWidth || maximumY !== stageHeight) {
        throw new RangeError('capture stage does not match monitor extents');
    }
    requireConnected(monitors);

    // clutter_stage_get_capture_final_size() uses these same StageView scales.
    // GNOME logical layout mode matches the monitor geometry scales; physical
    // mode leaves every StageView at scale 1. Reject any mixed/unknown model.
    const configuredScales = monitors.map(monitor => monitor.scale)
        .sort((left, right) => left - right);
    const physicalLayout = stageViewScales.every(scale =>
        Math.abs(scale - PHYSICAL_LAYOUT_CAPTURE_SCALE) <= SCALE_EPSILON);
    const unmatchedStageViewScales = [...stageViewScales];
    const configuredScaleMultiplicityCovered = configuredScales.every(scale => {
        const match = unmatchedStageViewScales.findIndex(viewScale =>
            Math.abs(viewScale - scale) <= SCALE_EPSILON);
        if (match < 0)
            return false;
        unmatchedStageViewScales.splice(match, 1);
        return true;
    });
    const logicalLayout = configuredScaleMultiplicityCovered &&
        stageViewScales.every(viewScale => configuredScales.some(scale =>
            Math.abs(viewScale - scale) <= SCALE_EPSILON));
    if (!physicalLayout && !logicalLayout) {
        throw new RangeError(
            'stage view scales do not match a GNOME physical/logical layout');
    }
    const layoutMode = physicalLayout && logicalLayout
        ? 'scale-one'
        : physicalLayout ? 'physical' : 'logical';
    const captureScale = Math.max(...stageViewScales);
    const serialized = {
        version: 1,
        generation: topologyGeneration,
        stage_width: stageWidth,
        stage_height: stageHeight,
        monitors: monitors.map(monitor => ({...monitor})),
        stage_view_scales: [...stageViewScales],
    };
    const signature = JSON.stringify(serialized);

    return Object.freeze({
        stageWidth,
        stageHeight,
        monitors: Object.freeze(monitors),
        generation: topologyGeneration,
        stageViewScales,
        captureScale,
        layoutMode,
        signature,
        id: topologyId(signature),
    });
}

export function captureTopologiesEqual(left, right) {
    return typeof left?.signature === 'string' &&
        left.signature === right?.signature;
}

/**
 * Bound the compositor allocation before screenshot_stage_to_content().
 * The selected-region cap runs after this whole-stage texture already exists.
 */
export function validateStageCaptureBudget(topology) {
    if (typeof topology?.signature !== 'string')
        throw new TypeError('capture topology is unavailable');

    const textureWidth = Math.round(
        topology.stageWidth * topology.captureScale);
    const textureHeight = Math.round(
        topology.stageHeight * topology.captureScale);
    const pixelCount = textureWidth * textureHeight;
    if (!Number.isSafeInteger(textureWidth) ||
        !Number.isSafeInteger(textureHeight) ||
        !Number.isSafeInteger(pixelCount) ||
        textureWidth < 1 || textureHeight < 1 ||
        textureWidth > MAX_CAPTURE_TEXTURE_DIMENSION ||
        textureHeight > MAX_CAPTURE_TEXTURE_DIMENSION ||
        pixelCount > MAX_CAPTURE_PIXEL_COUNT) {
        throw new RangeError('whole-stage capture exceeds allocation bounds');
    }

    return Object.freeze({
        captureScale: topology.captureScale,
        textureWidth,
        textureHeight,
        pixelCount,
    });
}

/**
 * Verify Shell.Screenshot's GNOME 50/51 stage-texture contract.
 *
 * Clutter renders one uniform texture sized round(stage * returnedScale).
 * The immutable topology contains the exact StageView scales read through the
 * same public Clutter objects used by the compositor. Lower-scale views are
 * compositor-resampled into the maximum-scale texture; neither GNOME layout
 * mode returns per-output buffers.
 */
export function validateCapturedStage(topology, scale, textureSize) {
    if (typeof topology?.signature !== 'string')
        throw new TypeError('capture topology is unavailable');
    scale = requireScale(scale, 'capture scale');
    if (Math.abs(scale - topology.captureScale) > SCALE_EPSILON) {
        throw new RangeError(
            'capture scale differs from the frozen maximum stage view scale');
    }

    validateStageCaptureBudget(topology);
    const textureWidth = requireInteger(
        textureSize?.width, 1, MAX_COORDINATE, 'texture.width');
    const textureHeight = requireInteger(
        textureSize?.height, 1, MAX_COORDINATE, 'texture.height');
    const expectedWidth = Math.round(
        topology.stageWidth * topology.captureScale);
    const expectedHeight = Math.round(
        topology.stageHeight * topology.captureScale);
    if (textureWidth !== expectedWidth || textureHeight !== expectedHeight) {
        throw new RangeError(
            'captured texture dimensions differ from the stage-scale contract');
    }

    return Object.freeze({
        scale,
        layoutMode: topology.layoutMode,
        textureWidth,
        textureHeight,
    });
}

export function topologyLogFields(topology) {
    if (typeof topology?.signature !== 'string')
        throw new TypeError('capture topology is unavailable');
    return {
        topology_id: topology.id,
        topology_generation: topology.generation,
        stage_width: topology.stageWidth,
        stage_height: topology.stageHeight,
        monitors: topology.monitors.length,
        stage_view_scales: [...topology.stageViewScales],
        capture_scale: topology.captureScale,
        capture_layout_mode: topology.layoutMode,
        monitor_layout: topology.monitors.map(monitor => ({...monitor})),
    };
}
