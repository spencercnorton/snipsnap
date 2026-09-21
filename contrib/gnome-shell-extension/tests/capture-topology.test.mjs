// SPDX-License-Identifier: GPL-3.0-or-later

import assert from 'node:assert/strict';
import test from 'node:test';

import {
    captureTopologiesEqual,
    snapshotCaptureTopology,
    topologyLogFields,
    validateCapturedStage,
    validateStageCaptureBudget,
} from '../snipsnap-shell-bridge/capture-topology.js';

function monitor(index, x, y, width, height, scale) {
    return {index, x, y, width, height, geometry_scale: scale};
}

function snapshot(stageSize, monitors, stageViewScales, generation = 1) {
    return snapshotCaptureTopology(
        stageSize, monitors, generation, stageViewScales);
}

test('reference three-monitor stage has one stable scale-1 texture contract', () => {
    const monitors = [
        monitor(0, 0, 0, 3840, 2160, 1),
        monitor(1, 3840, 0, 3840, 2160, 1),
        monitor(2, 7680, 0, 3840, 2160, 1),
    ];
    const topology = snapshot(
        {width: 11520, height: 2160}, monitors, [1, 1, 1]);

    assert.deepEqual(validateCapturedStage(topology, 1, {
        width: 11520,
        height: 2160,
    }), {
        scale: 1,
        layoutMode: 'scale-one',
        textureWidth: 11520,
        textureHeight: 2160,
    });
    assert.deepEqual(topology.stageViewScales, [1, 1, 1]);
    assert.equal(topology.captureScale, 1);
    assert.match(topology.id, /^v1-[0-9a-f]{8}$/);
    assert.deepEqual(topologyLogFields(topology).monitor_layout, [
        {index: 0, x: 0, y: 0, width: 3840, height: 2160, scale: 1},
        {index: 1, x: 3840, y: 0, width: 3840, height: 2160, scale: 1},
        {index: 2, x: 7680, y: 0, width: 3840, height: 2160, scale: 1},
    ]);
});

test('logical layout uses the maximum scale for one uniform texture', () => {
    // The end views are rotated 270/90 degrees. Every physical mode remains
    // above Mutter's 800x480 minimum logical area at its configured scale.
    const monitors = [
        monitor(0, 0, 0, 480, 800, 1),
        monitor(1, 480, 160, 800, 480, 1.25),
        monitor(2, 1280, 160, 800, 480, 1.5),
        monitor(3, 2080, 0, 480, 800, 2),
    ];
    const topology = snapshot(
        {width: 2560, height: 800}, monitors, [2, 1.5, 1, 1.25]);

    assert.deepEqual(topology.stageViewScales, [1, 1.25, 1.5, 2]);
    assert.equal(topology.captureScale, 2);
    assert.equal(topology.layoutMode, 'logical');
    assert.deepEqual(validateStageCaptureBudget(topology), {
        captureScale: 2,
        textureWidth: 5120,
        textureHeight: 1600,
        pixelCount: 8_192_000,
    });
    assert.deepEqual(validateCapturedStage(topology, 2, {
        width: 5120,
        height: 1600,
    }), {
        scale: 2,
        layoutMode: 'logical',
        textureWidth: 5120,
        textureHeight: 1600,
    });
});

test('physical layout binds the exact unscaled StageViews', () => {
    // Physical layout uses transformed mode dimensions and only integer output
    // scales. geometry_scale 2 does not make its StageView scale 2.
    const topology = snapshot({width: 3640, height: 1600}, [
        monitor(0, 0, 0, 480, 800, 1),
        monitor(1, 480, 100, 1000, 600, 1),
        monitor(2, 1480, 40, 1200, 720, 1),
        monitor(3, 2680, 0, 960, 1600, 2),
    ], [1, 1, 1, 1]);

    assert.equal(topology.layoutMode, 'physical');
    assert.equal(topology.captureScale, 1);
    assert.deepEqual(validateStageCaptureBudget(topology), {
        captureScale: 1,
        textureWidth: 3640,
        textureHeight: 1600,
        pixelCount: 5_824_000,
    });
    assert.deepEqual(validateCapturedStage(topology, 1, {
        width: 3640,
        height: 1600,
    }), {
        scale: 1,
        layoutMode: 'physical',
        textureWidth: 3640,
        textureHeight: 1600,
    });
    assert.throws(() => validateCapturedStage(topology, 2, {
        width: 7280,
        height: 3200,
    }), /maximum stage view scale/);
});

test('mirrored and tiled outputs may add StageViews per logical monitor', () => {
    const monitors = [
        monitor(0, 0, 0, 100, 100, 1),
        monitor(1, 100, 0, 100, 100, 1.25),
    ];
    const logical = snapshot(
        {width: 200, height: 100}, monitors, [1.25, 1, 1.25]);
    const physical = snapshot(
        {width: 200, height: 100}, monitors, [1, 1, 1]);

    assert.equal(logical.layoutMode, 'logical');
    assert.deepEqual(logical.stageViewScales, [1, 1.25, 1.25]);
    assert.equal(logical.captureScale, 1.25);
    assert.equal(physical.layoutMode, 'physical');
    assert.deepEqual(physical.stageViewScales, [1, 1, 1]);
    assert.notEqual(logical.id, physical.id);
});

test('staggered edge-connected monitors may leave holes in the stage', () => {
    const topology = snapshot({width: 300, height: 200}, [
        monitor(0, 0, 0, 100, 100, 1),
        monitor(1, 100, 50, 100, 100, 1.25),
        monitor(2, 200, 100, 100, 100, 1.5),
    ], [1, 1.25, 1.5]);

    assert.equal(topology.stageWidth, 300);
    assert.equal(topology.stageHeight, 200);
    assert.equal(topology.captureScale, 1.5);
    assert.doesNotThrow(() => validateCapturedStage(topology, 1.5, {
        width: 450,
        height: 300,
    }));
});

test('topology equality is order-independent but detects scale and geometry', () => {
    const first = snapshot({width: 200, height: 100}, [
        monitor(1, 100, 0, 100, 100, 1.25),
        monitor(0, 0, 0, 100, 100, 1),
    ], [1.25, 1]);
    const reordered = snapshot({width: 200, height: 100}, [
        monitor(0, 0, 0, 100, 100, 1),
        monitor(1, 100, 0, 100, 100, 1.25),
    ], [1, 1.25]);
    const rescaled = snapshot({width: 200, height: 100}, [
        monitor(0, 0, 0, 100, 100, 1),
        monitor(1, 100, 0, 100, 100, 1.5),
    ], [1, 1.5]);
    const physical = snapshot({width: 200, height: 100}, [
        monitor(0, 0, 0, 100, 100, 1),
        monitor(1, 100, 0, 100, 100, 1.25),
    ], [1, 1]);
    const regenerated = snapshot({width: 200, height: 100}, [
        monitor(0, 0, 0, 100, 100, 1),
        monitor(1, 100, 0, 100, 100, 1.25),
    ], [1, 1.25], 2);

    assert.equal(captureTopologiesEqual(first, reordered), true);
    assert.equal(captureTopologiesEqual(first, rescaled), false);
    assert.equal(captureTopologiesEqual(first, physical), false);
    assert.equal(captureTopologiesEqual(first, regenerated), false);
    assert.equal(topologyLogFields(regenerated).topology_generation, 2);
});

test('invalid Mutter layouts and stale texture contracts fail closed', () => {
    assert.throws(() => snapshot({width: 200, height: 100}, [
        monitor(0, -100, 0, 100, 100, 1),
        monitor(1, 0, 0, 100, 100, 1),
    ], [1, 1]), /monitor\[0\]\.x/);
    assert.throws(() => snapshot({width: 300, height: 100}, [
        monitor(0, 0, 0, 100, 100, 1),
        monitor(1, 200, 0, 100, 100, 1),
    ], [1, 1]), /disconnected/);
    assert.throws(() => snapshot({width: 150, height: 100}, [
        monitor(0, 0, 0, 100, 100, 1),
        monitor(1, 50, 0, 100, 100, 1),
    ], [1, 1]), /overlap/);

    assert.throws(() => snapshot({width: 200, height: 100}, [
        monitor(0, 0, 0, 100, 100, 1),
        monitor(1, 100, 0, 100, 100, 1.25),
    ], [1]), /stage view count/);
    assert.throws(() => snapshot({width: 200, height: 100}, [
        monitor(0, 0, 0, 100, 100, 1),
        monitor(1, 100, 0, 100, 100, 1.25),
    ], [1, 1.125]), /physical\/logical layout/);
    assert.throws(() => snapshot({width: 300, height: 100}, [
        monitor(0, 0, 0, 100, 100, 1),
        monitor(1, 100, 0, 100, 100, 1),
        monitor(2, 200, 0, 100, 100, 2),
    ], [1, 2, 2]), /physical\/logical layout/);
    assert.throws(() => snapshot({width: 200, height: 100}, [
        monitor(0, 0, 0, 100, 100, 1),
        monitor(1, 100, 0, 100, 100, 1.25),
    ], [1, 1.25, 1.5]), /physical\/logical layout/);
    assert.throws(() => snapshot({width: 100, height: 100}, [
        monitor(0, 0, 0, 100, 100, 1),
    ], Array(65).fill(1)), /stage view count/);

    const topology = snapshot({width: 200, height: 100}, [
        monitor(0, 0, 0, 100, 100, 1),
        monitor(1, 100, 0, 100, 100, 1.25),
    ], [1, 1.25]);
    assert.throws(() => validateCapturedStage(topology, 1, {
        width: 200,
        height: 100,
    }), /maximum stage view scale/);
    assert.throws(() => validateCapturedStage(topology, 1.25, {
        width: 249,
        height: 125,
    }), /texture dimensions/);
    const oversized = snapshot({width: 9000, height: 1000}, [
        monitor(0, 0, 0, 9000, 1000, 4),
    ], [4]);
    assert.throws(() => validateStageCaptureBudget(oversized),
        /allocation bounds/);

    const physicalSameMonitor = snapshot({width: 9000, height: 1000}, [
        monitor(0, 0, 0, 9000, 1000, 4),
    ], [1]);
    assert.equal(validateStageCaptureBudget(physicalSameMonitor).pixelCount,
        9_000_000);
});
