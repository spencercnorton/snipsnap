// SPDX-License-Identifier: GPL-3.0-or-later

/**
 * Desktop accent colour -> overlay styling.
 *
 * GNOME 47+ exposes a named accent at org.gnome.desktop.interface accent-color.
 * The Shell theme does not publish it as a CSS variable, so the overlay has to
 * resolve it here and apply inline styles. Kept free of gi:// imports so the
 * colour maths stays unit-testable under plain node.
 */

// libadwaita's base accent colours (GNOME 47+ named accents).
export const ACCENT_COLORS = {
    blue: '#3584e4',
    teal: '#2190a4',
    green: '#3a944a',
    yellow: '#c88800',
    orange: '#ed5b00',
    red: '#e62d42',
    pink: '#d56199',
    purple: '#9141ac',
    slate: '#6f8396',
};

export const DEFAULT_ACCENT = 'blue';

// The selection sits on a screenshot dimmed to 46%, where the base accents are
// too dark to read. libadwaita solves the same problem with lighter
// "standalone" variants for dark backgrounds; mixing toward white approximates
// that without carrying a second table that could drift out of sync.
const CONTRAST_MIX = 0.32;

function parseHex(hex) {
    const match = /^#([0-9a-f]{6})$/i.exec(String(hex ?? ''));
    if (!match)
        throw new TypeError(`not a #rrggbb colour: ${hex}`);
    const value = parseInt(match[1], 16);
    return [(value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff];
}

function clampUnit(value) {
    if (!Number.isFinite(value))
        return 0;
    return Math.min(Math.max(value, 0), 1);
}

/** Resolve a named accent to #rrggbb, falling back for unknown names. */
export function resolveAccent(name) {
    return ACCENT_COLORS[name] ?? ACCENT_COLORS[DEFAULT_ACCENT];
}

/** Mix a colour toward white; amount 0 keeps it, 1 returns white. */
export function mixWithWhite(hex, amount) {
    const ratio = clampUnit(amount);
    const channels = parseHex(hex).map(
        channel => Math.round(channel + (255 - channel) * ratio));
    return `#${channels.map(c => c.toString(16).padStart(2, '0')).join('')}`;
}

/** CSS rgba() string for a #rrggbb colour at the given alpha. */
export function withAlpha(hex, alpha) {
    const [red, green, blue] = parseHex(hex);
    return `rgba(${red}, ${green}, ${blue}, ${clampUnit(alpha)})`;
}

/**
 * Inline styles for the accent-carrying overlay actors. One place owns the
 * look, so the extension only has to apply what it is handed.
 */
export function overlayAccentStyles(accentName) {
    const accent = mixWithWhite(resolveAccent(accentName), CONTRAST_MIX);
    return {
        selection: `border: 2px solid ${accent}; ` +
            `background-color: ${withAlpha(accent, 0.08)};`,
        label: `border: 1px solid ${withAlpha(accent, 0.55)};`,
    };
}
