// SPDX-License-Identifier: GPL-3.0-or-later

import assert from 'node:assert/strict';
import test from 'node:test';

import {
    CaptureLifecycle,
    unlockedActionModes,
} from '../snipsnap-shell-bridge/capture-lifecycle.js';

test('capture IDs stay monotonic across disable and re-enable', () => {
    const lifecycle = new CaptureLifecycle();

    lifecycle.enable();
    assert.equal(lifecycle.nextCaptureId(), 1);
    assert.equal(lifecycle.nextCaptureId(), 2);

    lifecycle.disable();
    lifecycle.enable();
    assert.equal(lifecycle.nextCaptureId(), 3);
});

test('a stale completion cannot clear a newer lifecycle request', () => {
    const lifecycle = new CaptureLifecycle();

    lifecycle.enable();
    const oldRequest = lifecycle.beginRequest(lifecycle.nextCaptureId());
    assert.ok(oldRequest);
    assert.equal(lifecycle.busy, true);

    lifecycle.disable();
    lifecycle.enable();
    const newRequest = lifecycle.beginRequest(lifecycle.nextCaptureId());
    assert.ok(newRequest);
    assert.equal(lifecycle.isCurrent(oldRequest), false);
    assert.equal(lifecycle.isCurrent(newRequest), true);

    assert.equal(lifecycle.finishRequest(oldRequest), false);
    assert.equal(lifecycle.busy, true);
    assert.equal(lifecycle.isCurrent(newRequest), true);

    assert.equal(lifecycle.finishRequest(newRequest), true);
    assert.equal(lifecycle.busy, false);
});

test('an invalidated request cannot clear its replacement', () => {
    const lifecycle = new CaptureLifecycle();

    lifecycle.enable();
    const invalidatedRequest = lifecycle.beginRequest(
        lifecycle.nextCaptureId());
    lifecycle.invalidateRequest();
    const replacementRequest = lifecycle.beginRequest(
        lifecycle.nextCaptureId());

    assert.equal(lifecycle.finishRequest(invalidatedRequest), false);
    assert.equal(lifecycle.busy, true);
    assert.equal(lifecycle.isCurrent(replacementRequest), true);
});

test('unlocked action modes exclude login, lock, and unlock modes', () => {
    const actionMode = {
        NORMAL: 1 << 0,
        OVERVIEW: 1 << 1,
        POPUP: 1 << 2,
        LOGIN_SCREEN: 1 << 3,
        LOCK_SCREEN: 1 << 4,
        UNLOCK_SCREEN: 1 << 5,
        ALL: (1 << 6) - 1,
    };

    const modes = unlockedActionModes(actionMode);

    assert.equal(modes & actionMode.NORMAL, actionMode.NORMAL);
    assert.equal(modes & actionMode.OVERVIEW, actionMode.OVERVIEW);
    assert.equal(modes & actionMode.POPUP, actionMode.POPUP);
    assert.equal(modes & actionMode.LOGIN_SCREEN, 0);
    assert.equal(modes & actionMode.LOCK_SCREEN, 0);
    assert.equal(modes & actionMode.UNLOCK_SCREEN, 0);
});
