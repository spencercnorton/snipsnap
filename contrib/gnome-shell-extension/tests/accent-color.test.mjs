// SPDX-License-Identifier: GPL-3.0-or-later

import assert from 'node:assert/strict';
import test from 'node:test';

import {
    ACCENT_COLORS,
    DEFAULT_ACCENT,
    mixWithWhite,
    overlayAccentStyles,
    resolveAccent,
    withAlpha,
} from '../snipsnap-shell-bridge/accent-color.js';

test('every named GNOME accent resolves to a #rrggbb colour', () => {
    for (const [name, value] of Object.entries(ACCENT_COLORS)) {
        assert.match(value, /^#[0-9a-f]{6}$/, `${name} is not #rrggbb`);
        assert.equal(resolveAccent(name), value);
    }
});

test('an unknown or missing accent falls back instead of throwing', () => {
    const fallback = ACCENT_COLORS[DEFAULT_ACCENT];
    assert.equal(resolveAccent('chartreuse'), fallback);
    assert.equal(resolveAccent(undefined), fallback);
    assert.equal(resolveAccent(null), fallback);
    assert.equal(resolveAccent(''), fallback);
});

test('mixing toward white lightens without leaving the channel range', () => {
    assert.equal(mixWithWhite('#000000', 0), '#000000');
    assert.equal(mixWithWhite('#000000', 1), '#ffffff');
    // 53,132,228 -> 154,194,242: half-way channels round, they do not floor.
    assert.equal(mixWithWhite('#3584e4', 0.5), '#9ac2f2');
    // Out-of-range amounts clamp rather than producing invalid colours.
    assert.equal(mixWithWhite('#3584e4', -5), '#3584e4');
    assert.equal(mixWithWhite('#3584e4', 5), '#ffffff');
});

test('withAlpha emits a CSS rgba() St can parse', () => {
    assert.equal(withAlpha('#3584e4', 0.5), 'rgba(53, 132, 228, 0.5)');
    assert.equal(withAlpha('#ffffff', 2), 'rgba(255, 255, 255, 1)');
});

test('malformed colours are rejected, not silently rendered', () => {
    assert.throws(() => mixWithWhite('3584e4', 0.5), TypeError);
    assert.throws(() => withAlpha('#xyzxyz', 0.5), TypeError);
});

test('overlay styles are distinct per accent and syntactically complete', () => {
    const orange = overlayAccentStyles('orange');
    const purple = overlayAccentStyles('purple');
    assert.notEqual(orange.selection, purple.selection);
    for (const styles of [orange, purple]) {
        assert.match(styles.selection, /^border: 2px solid #[0-9a-f]{6}; /);
        assert.match(styles.selection, /background-color: rgba\([\d, .]+\);$/);
        assert.match(styles.label, /^border: 1px solid rgba\([\d, .]+\);$/);
    }
});
