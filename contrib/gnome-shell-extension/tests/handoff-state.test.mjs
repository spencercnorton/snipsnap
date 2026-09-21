// SPDX-License-Identifier: GPL-3.0-or-later

import assert from 'node:assert/strict';
import test from 'node:test';

import {
    HandoffState,
    UncancellableWorkGate,
    editorWindowTitle,
    matchesEditorWindow,
    scaleToFraction,
    selectionToPixelRegion,
} from '../snipsnap-shell-bridge/handoff-state.js';

test('pixel region rounds outward at fractional scale', () => {
    assert.deepEqual(selectionToPixelRegion(
        {x: 3, y: 5, width: 7, height: 9},
        1.25,
        {width: 100, height: 100},
        {width: 80, height: 80}
    ), {
        x: 3,
        y: 6,
        width: 10,
        height: 12,
        logicalX: 3,
        logicalY: 5,
        logicalWidth: 7,
        logicalHeight: 9,
    });
    assert.deepEqual(scaleToFraction(1.25), {
        numerator: 5,
        denominator: 4,
    });
    assert.deepEqual(scaleToFraction(Math.fround(4 / 3)), {
        numerator: 4,
        denominator: 3,
    });
    assert.deepEqual(scaleToFraction(5 / 3), {
        numerator: 5,
        denominator: 3,
    });
    assert.deepEqual(scaleToFraction(7 / 4), {
        numerator: 7,
        denominator: 4,
    });
    assert.deepEqual(scaleToFraction(4 - Number.EPSILON), {
        numerator: 4,
        denominator: 1,
    });
    assert.throws(() => scaleToFraction(1.333332), /canonical GNOME/);
});

test('pixel region rejects geometry outside the captured texture', () => {
    assert.throws(() => selectionToPixelRegion(
        {x: -3, y: 95, width: 10, height: 10},
        1,
        {width: 100, height: 100},
        {width: 100, height: 100}
    ), /outside the captured logical stage/);
    assert.throws(() => selectionToPixelRegion(
        {x: 1000, y: 0, width: 1, height: 1},
        1,
        {width: 100, height: 100},
        {width: 100, height: 100}
    ), /outside the captured logical stage/);
    assert.throws(() => selectionToPixelRegion(
        {x: 99, y: 99, width: 2, height: 1},
        1,
        {width: 100, height: 100},
        {width: 100, height: 100}
    ), /outside the captured logical stage/);
});

test('fractional selection may end at a rounded stage texture edge', () => {
    assert.deepEqual(selectionToPixelRegion(
        {x: 99, y: 79, width: 2, height: 2},
        1.25,
        {width: 126, height: 101},
        {width: 101, height: 81}
    ), {
        x: 123,
        y: 98,
        width: 3,
        height: 3,
        logicalX: 99,
        logicalY: 79,
        logicalWidth: 2,
        logicalHeight: 2,
    });
});

test('float32 GNOME scale uses exact rational crop boundaries', () => {
    assert.deepEqual(selectionToPixelRegion(
        {x: 3, y: 6, width: 3, height: 3},
        Math.fround(4 / 3),
        {width: 400, height: 400},
        {width: 300, height: 300}
    ), {
        x: 4,
        y: 8,
        width: 4,
        height: 4,
        logicalX: 3,
        logicalY: 6,
        logicalWidth: 3,
        logicalHeight: 3,
    });
});

test('pixel and scale bounds fail before compositor readback', () => {
    assert.throws(() => selectionToPixelRegion(
        {x: 0, y: 0, width: 8000, height: 5000},
        1,
        {width: 8000, height: 5000},
        {width: 8000, height: 5000}
    ), /pixel count/);
    assert.throws(() => selectionToPixelRegion(
        {x: 0, y: 0, width: 1, height: 1},
        4.1,
        {width: 10, height: 10},
        {width: 10, height: 10}
    ), /scale/);
});

test('handoff gate blocks repeats and ignores stale completion', () => {
    const state = new HandoffState();
    const oldRequest = Object.freeze({captureId: 1});
    const oldOperation = state.begin(oldRequest);

    assert.ok(oldOperation);
    assert.equal(state.busy, true);
    assert.equal(state.begin(oldRequest), null);

    state.cancel();
    const newRequest = Object.freeze({captureId: 2});
    const newOperation = state.begin(newRequest);
    assert.ok(newOperation);
    assert.equal(state.finish(oldOperation), false);
    assert.equal(state.isCurrent(newOperation, newRequest), true);
    assert.equal(state.finish(newOperation), true);
    assert.equal(state.busy, false);
});

test('uncancellable work stays busy until its exact owner settles', () => {
    const gate = new UncancellableWorkGate();
    const operation = gate.begin();

    assert.ok(operation);
    assert.equal(gate.busy, true);
    assert.equal(gate.begin(), null);
    assert.equal(gate.finish(Object.freeze({})), false);
    assert.equal(gate.busy, true);
    assert.equal(gate.finish(operation), true);
    assert.equal(gate.busy, false);
});

test('editor focus match requires peer PID, normal type, and exact identity', () => {
    const normal = 0;
    const peerPid = 4242;
    assert.equal(matchesEditorWindow({
        pid: peerPid,
        windowType: normal,
        title: editorWindowTitle(17),
        identities: ['tech.norvi.snipsnap'],
    }, peerPid, normal, 17), true);

    assert.equal(matchesEditorWindow({
        pid: peerPid,
        windowType: 1,
        title: editorWindowTitle(17),
        identities: ['snipsnap'],
    }, peerPid, normal, 17), false);
    assert.equal(matchesEditorWindow({
        pid: peerPid + 1,
        windowType: normal,
        title: editorWindowTitle(17),
        identities: ['snipsnap'],
    }, peerPid, normal, 17), false);
    assert.equal(matchesEditorWindow({
        pid: peerPid,
        windowType: normal,
        title: editorWindowTitle(17),
        identities: ['not-snipsnap'],
    }, peerPid, normal, 17), false);
    assert.equal(matchesEditorWindow({
        pid: peerPid,
        windowType: normal,
        title: editorWindowTitle(18),
        identities: ['snipsnap'],
    }, peerPid, normal, 17), false);
    assert.equal(editorWindowTitle(17n),
        'SnipSnap [capture-id=17]');
});
