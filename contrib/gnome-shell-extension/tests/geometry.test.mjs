// SPDX-License-Identifier: GPL-3.0-or-later

import assert from 'node:assert/strict';
import test from 'node:test';

import {
    formatDimensions,
    labelPosition,
    selectionFromPoints,
    shadeRectangles,
} from '../snipsnap-shell-bridge/geometry.js';

test('selection crosses both seams in the reference three-monitor topology', () => {
    const selection = selectionFromPoints(
        {x: 3700, y: 100},
        {x: 7800, y: 2100},
        {width: 11520, height: 2160}
    );

    assert.deepEqual(selection, {
        x: 3700,
        y: 100,
        width: 4101,
        height: 2001,
    });
    assert.equal(formatDimensions(selection), '4101 × 2001');
});

test('reverse drags normalize and clamp to the stage', () => {
    assert.deepEqual(selectionFromPoints(
        {x: 12000, y: 2200},
        {x: -50, y: -25},
        {width: 11520, height: 2160}
    ), {
        x: 0,
        y: 0,
        width: 11520,
        height: 2160,
    });
});

test('a click produces a visible one-pixel selection', () => {
    assert.deepEqual(selectionFromPoints(
        {x: 42.9, y: 84.2},
        {x: 42.9, y: 84.2},
        {width: 100, height: 100}
    ), {x: 42, y: 84, width: 1, height: 1});
});

test('shade rectangles cover exactly the area outside the selection', () => {
    const bounds = {width: 11520, height: 2160};
    const selection = {x: 3700, y: 100, width: 4101, height: 2001};
    const shades = shadeRectangles(selection, bounds);
    const shadeArea = Object.values(shades)
        .reduce((sum, rect) => sum + rect.width * rect.height, 0);

    assert.equal(shadeArea,
        bounds.width * bounds.height - selection.width * selection.height);
    for (const rect of Object.values(shades)) {
        assert.ok(rect.width >= 0);
        assert.ok(rect.height >= 0);
    }
});

test('dimension label stays inside the stage', () => {
    assert.deepEqual(labelPosition(
        {x: 11400, y: 2100, width: 100, height: 50},
        {width: 140, height: 40},
        {width: 11520, height: 2160}
    ), {x: 11368, y: 2108});
});
