// SPDX-License-Identifier: GPL-3.0-or-later
//
// README media rig: drives the same headless GNOME Shell 50 harness as
// gnome_shell_bridge_e2e.js but stages a demonstration desktop (photo
// wallpaper, real GTK windows with invented content, two 1600x900 outputs)
// and records stills plus numbered flow frames for the public README.
// Nothing here asserts; a missing picture is reported, not fatal.
// Run it with scripts/demo-capture.sh.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import MetaTest from 'gi://MetaTest';
import Shell from 'gi://Shell';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

import {
    dbusOwnerPid,
    findOverlay,
    findSelection,
    focusEditorWindow,
    pressChord,
    pressKey,
    waitForProcess,
    waitUntil,
    windowFrame,
} from './gnome_shell_bridge_e2e.js';

const UUID = 'snipsnap-shell-bridge@kleos.norvi.tech';
const MONITOR = {width: 1600, height: 900};
const DISPLAY_CONFIG = 'org.gnome.Mutter.DisplayConfig';
const DISPLAY_CONFIG_PATH = '/org/gnome/Mutter/DisplayConfig';
// Selection crossing the seam between the two outputs (stage coordinates).
const SELECTION = {start: {x: 580, y: 100}, end: {x: 2160, y: 770}};
const WINDOWS = {
    editor: {monitor: 0, x: 600, y: 110, width: 900, height: 640},
    calculator: {monitor: 1, x: 1720, y: 130, width: 360, height: 616},
};
// Editor toolbar geometry from RegionEditorWindow: 8px margin, 4px layout
// margin, 36px buttons with 4px spacing. Order is the hostTools list.
// "selection" is the outline rectangle; TYPE_RECTANGLE is filled.
const TOOL_INDEX = {arrow: 2, rectangle: 3, text: 7, pixelate: 8};
const OUTPUT_DIR = GLib.getenv('GNOME_SHELL_E2E_OUTPUT_DIR');
const DEMO_HOME = GLib.getenv('SNIPSNAP_DEMO_HOME') ?? '/home/demo';

let testMonitors = [];
let daemon = null;
let apps = [];
let portalMock = null;
const frames = [];
const problems = [];

function unpackVariants(value) {
    if (value instanceof GLib.Variant)
        return unpackVariants(value.deepUnpack());
    if (Array.isArray(value))
        return value.map(unpackVariants);
    if (value !== null && typeof value === 'object') {
        return Object.fromEntries(Object.entries(value).map(
            ([key, child]) => [key, unpackVariants(child)]));
    }
    return value;
}

function sessionCall(name, path, iface, method, parameters = null) {
    return new Promise((resolve, reject) => {
        Gio.DBus.session.call(name, path, iface, method, parameters, null,
            Gio.DBusCallFlags.NO_AUTO_START, 5000, null,
            (connection, result) => {
                try {
                    resolve(connection.call_finish(result));
                } catch (error) {
                    reject(error);
                }
            });
    });
}

async function addTestMonitor(expectedCount) {
    const changed = new Promise(resolve => {
        const signalId = Main.layoutManager.connect('monitors-changed', () => {
            Main.layoutManager.disconnect(signalId);
            resolve();
        });
    });
    testMonitors.push(MetaTest.TestMonitor.new(
        global.context, MONITOR.width, MONITOR.height, 60.0));
    await changed;
    await waitUntil(() => Main.layoutManager.monitors.length === expectedCount,
        3000, `${expectedCount} monitors`);
}

/** Two 1600x900 outputs side by side; the tool's 1280x720 start monitor is dropped. */
async function configureMonitors() {
    await addTestMonitor(2);
    await addTestMonitor(3);
    const state = unpackVariants(await sessionCall(
        DISPLAY_CONFIG, DISPLAY_CONFIG_PATH, DISPLAY_CONFIG, 'GetCurrentState'));
    const selected = [];
    for (const [identity, modes] of state[1]) {
        for (const mode of modes) {
            if (Number(mode[1]) === MONITOR.width &&
                Number(mode[2]) === MONITOR.height)
                selected.push({connector: String(identity[0]), modeId: String(mode[0])});
        }
    }
    if (selected.length !== 2)
        throw new Error(`expected 2 ${MONITOR.width}x${MONITOR.height} monitors, found ${selected.length}`);
    const logical = selected.map((monitor, index) => [
        index * MONITOR.width, 0, 1.0, 0, index === 0,
        [[monitor.connector, monitor.modeId, {}]],
    ]);
    await sessionCall(DISPLAY_CONFIG, DISPLAY_CONFIG_PATH, DISPLAY_CONFIG,
        'ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
            [Number(state[0]), 1, logical, {'layout-mode': new GLib.Variant('u', 1)}]));
    await waitUntil(() => Main.layoutManager.monitors.length === 2 &&
        Math.floor(global.stage.width) === 2 * MONITOR.width, 5000, 'two-output stage');
    await Scripting.waitLeisure();
}

function sessionEnv(extra = {}) {
    return {
        XDG_SESSION_TYPE: 'wayland',
        XDG_CURRENT_DESKTOP: 'GNOME',
        WAYLAND_DISPLAY: 'gnome-shell-test-display',
        GDK_BACKEND: 'wayland',
        LIBGL_ALWAYS_SOFTWARE: '1',
        ADW_DISABLE_PORTAL: '1',
        HOME: DEMO_HOME,
        ...extra,
    };
}

function spawn(argv, extra = {}) {
    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.NONE});
    for (const [name, value] of Object.entries(sessionEnv(extra)))
        launcher.setenv(name, value, true);
    return launcher.spawnv(argv);
}

/**
 * Qt's gnome platform theme never turned the editor dark in this rig (the
 * portal mock's ReadAll/SettingChanged answers were read but not applied),
 * so the dark daemon uses the gtk3 platform theme, which follows the GTK
 * settings.ini written by setColorScheme.
 */
function spawnDaemon(dark = false) {
    const executable = GLib.getenv('SNIPSNAP_TEST_EXECUTABLE');
    const extra = {QT_QPA_PLATFORM: 'wayland'};
    if (dark)
        extra.QT_QPA_PLATFORMTHEME = 'gtk3';
    return spawn([executable], extra);
}

/**
 * Qt's GNOME theme asks xdg-desktop-portal Settings for the colour scheme.
 * The rig has no portal, so a python-dbusmock stand-in answers Read with the
 * value in $OUTPUT_DIR/color-scheme (0 light, 1 dark).
 */
async function startPortalMock() {
    GLib.file_set_contents(`${OUTPUT_DIR}/color-scheme`, '0');
    portalMock = spawn(['python3', '-m', 'dbusmock', '--session',
        'org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop',
        'org.freedesktop.portal.Settings']);
    await waitUntil(() => dbusOwnerPid('org.freedesktop.portal.Desktop'),
        5000, 'portal settings mock');
    const code = `ret = dbus.UInt32(int(open('${OUTPUT_DIR}/color-scheme').read()), variant_level=2)`;
    const all = `ret = dbus.Dictionary({'org.freedesktop.appearance': dbus.Dictionary({'color-scheme': dbus.UInt32(int(open('${OUTPUT_DIR}/color-scheme').read()), variant_level=1)}, signature='sv')}, signature='sa{sv}')`;
    for (const [method, inSig, outSig, body] of [
        ['Read', 'ss', 'v', code], ['ReadOne', 'ss', 'v', code], ['ReadAll', 'as', 'a{sa{sv}}', all],
    ]) {
        await sessionCall('org.freedesktop.portal.Desktop',
            '/org/freedesktop/portal/desktop', 'org.freedesktop.DBus.Mock',
            'AddMethod', new GLib.Variant('(sssss)',
                ['org.freedesktop.portal.Settings', method, inSig, outSig, body]));
    }
}

async function setColorScheme(dark) {
    GLib.file_set_contents(`${OUTPUT_DIR}/color-scheme`, dark ? '1' : '0');
    new Gio.Settings({schema_id: 'org.gnome.desktop.interface'})
        .set_string('color-scheme', dark ? 'prefer-dark' : 'default');
    const gtk = `${GLib.get_user_config_dir()}/gtk-3.0`;
    GLib.mkdir_with_parents(gtk, 0o755);
    GLib.file_set_contents(`${gtk}/settings.ini`, '[Settings]\n' +
        `gtk-theme-name=${dark ? 'Adwaita-dark' : 'Adwaita'}\n` +
        `gtk-application-prefer-dark-theme=${dark ? 1 : 0}\n`);
    if (!portalMock)
        return;
    await sessionCall('org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop',
        'org.freedesktop.DBus.Mock', 'EmitSignal', new GLib.Variant('(sssav)', [
            'org.freedesktop.portal.Settings', 'SettingChanged', 'ssv', [
                new GLib.Variant('s', 'org.freedesktop.appearance'),
                new GLib.Variant('s', 'color-scheme'),
                new GLib.Variant('v', new GLib.Variant('u', dark ? 1 : 0)),
            ]]));
}

function findWindow(predicate) {
    for (const actor of global.get_window_actors()) {
        const window = actor.meta_window;
        if (window && predicate(window))
            return window;
    }
    return null;
}

/** Any SnipSnap editor window; the e2e helper pins capture id 1 only. */
function findEditor() {
    return findWindow(w => /^SnipSnap \[capture-id=\d+\]$/.test(w.get_title() ?? ''));
}

function wmClass(window) {
    return (window.get_wm_class() ?? window.get_gtk_application_id() ?? '').toLowerCase();
}

async function launchApp(argv, className, frame) {
    const before = new Set(global.get_window_actors().map(a => a.meta_window));
    apps.push(spawn(argv));
    const window = await waitUntil(() => findWindow(w =>
        !before.has(w) && wmClass(w).includes(className) &&
        w.get_compositor_private()?.mapped), 15000, `${className} window`);
    window.move_to_monitor(frame.monitor);
    // A cross-output move lands centred on the new output first, and a
    // resize below the app's minimum re-centres it: place, then move again.
    window.move_resize_frame(true, frame.x, frame.y, frame.width, frame.height);
    await Scripting.waitLeisure();
    await Scripting.sleep(200);
    window.move_frame(true, frame.x, frame.y);
    await Scripting.waitLeisure();
    await Scripting.sleep(200);
    console.log(`DEMO-WINDOW ${className} ${JSON.stringify(windowFrame(window))}`);
    return window;
}

async function hideOverview() {
    Main.overview.hide();
    await waitUntil(() => !Main.overview.visible && !Main.overview.animationInProgress,
        5000, 'overview hidden');
    await Scripting.waitLeisure();
}

function dismissNotifications() {
    for (const source of Main.messageTray.getSources())
        source.destroy();
}

/** Daemon config: no start-up help overlay in the editor, no capture notification. */
function writeSnipSnapConfig() {
    const directory = `${GLib.get_user_config_dir()}/snipsnap`;
    GLib.mkdir_with_parents(directory, 0o755);
    GLib.file_set_contents(`${directory}/snipsnap.ini`, [
        '[General]',
        'showHelp=false',
        'showDesktopNotification=false',
        'showStartupLaunchMessage=false',
        'drawColor=#e01b24',
        '',
    ].join('\n'));
}

function writeDocument() {
    GLib.mkdir_with_parents(`${DEMO_HOME}/Notes`, 0o755);
    GLib.file_set_contents(`${DEMO_HOME}/Notes/release-notes.md`, [
        '# Lantern 2.4 — release notes (draft)',
        '',
        '## Highlights',
        '',
        '- Region capture spans every monitor in one drag',
        '- The overlay follows the desktop accent colour',
        '- Pixelate hides tokens before a picture is shared',
        '- Copy or save straight from the editor',
        '',
        '## Before tagging',
        '',
        '- [x] Refresh the screenshots in the docs',
        '- [x] Run the multi-monitor smoke test',
        '- [ ] Ask the docs team to proofread',
        '- [ ] Tag v2.4.0 and publish the changelog',
        '',
    ].join('\n'));
}

async function screenshotArea(path, x, y, width, height) {
    const stream = Gio.File.new_for_path(path).replace(null, false,
        Gio.FileCreateFlags.REPLACE_DESTINATION, null);
    try {
        await new Shell.Screenshot().screenshot_area(x, y, width, height, stream);
    } finally {
        stream.close(null);
    }
}

function still(name) {
    return screenshotArea(`${OUTPUT_DIR}/${name}`, 0, 0,
        Math.floor(global.stage.width), Math.floor(global.stage.height));
}

async function frame(delayMs) {
    const file = `flow/frame-${String(frames.length).padStart(4, '0')}.png`;
    const [x, y] = global.get_pointer();
    await screenshotArea(`${OUTPUT_DIR}/${file}`, 0, 0,
        Math.floor(global.stage.width), Math.floor(global.stage.height));
    frames.push({file, delay_ms: delayMs, pointer: [Math.round(x), Math.round(y)]});
}

/** Like drag() but records a frame after every step while recording. */
async function dragRecorded(pointer, start, end, steps, recording, delayMs = 80) {
    const [originX, originY] = global.get_pointer();
    for (let step = 1; step <= 8; step++) {
        const ratio = step / 8;
        pointer.notify_absolute_motion(GLib.get_monotonic_time(),
            originX + (start.x - originX) * ratio, originY + (start.y - originY) * ratio);
        await Scripting.sleep(15);
    }
    await Scripting.sleep(50);
    pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY,
        Clutter.ButtonState.PRESSED);
    await Scripting.sleep(60);
    for (let step = 1; step <= steps; step++) {
        const ratio = step / steps;
        pointer.notify_absolute_motion(GLib.get_monotonic_time(),
            start.x + (end.x - start.x) * ratio, start.y + (end.y - start.y) * ratio);
        await Scripting.sleep(20);
        if (recording)
            await frame(delayMs);
    }
    pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY,
        Clutter.ButtonState.RELEASED);
    await Scripting.sleep(100);
}

async function click(pointer, x, y) {
    await dragRecorded(pointer, {x, y}, {x, y}, 1, false);
}

async function typeText(keyboard, text) {
    for (const character of text) {
        const keyval = Clutter.unicode_to_keysym(character.codePointAt(0));
        const shift = character !== character.toLowerCase();
        if (shift)
            await pressChord(keyboard, Clutter.KEY_Shift_L, keyval);
        else
            await pressKey(keyboard, keyval);
    }
}

function toolButton(editorFrame, name) {
    return {x: editorFrame.x + 30 + 40 * TOOL_INDEX[name], y: editorFrame.y + 30};
}

/** The Copy button leads the view-control strip, right-aligned under the toolbar. */
function copyButton(editorFrame) {
    return {x: editorFrame.x + editorFrame.width - 352 - 8 + 24, y: editorFrame.y + 76};
}

/** Canvas origin in stage coordinates: 1:1 view centred in the viewport. */
function canvasOrigin(editorFrame, selection) {
    return {
        x: editorFrame.x + Math.floor((editorFrame.width - selection.width) / 2),
        y: editorFrame.y + Math.floor((editorFrame.height - selection.height) / 2),
    };
}

async function captureFlow(keyboard, pointer, variant, recording) {
    await hideOverview();
    await pressKey(keyboard, Clutter.KEY_Print);
    const overlay = await waitUntil(findOverlay, 5000, 'capture overlay');
    if (recording) {
        await frame(400);
        await frame(400);
    }
    await dragRecorded(pointer, SELECTION.start, SELECTION.end, 12, recording);
    const selectionActor = await waitUntil(() => findSelection(overlay), 3000, 'selection');
    const selection = {
        x: Math.floor(selectionActor.x), y: Math.floor(selectionActor.y),
        width: Math.floor(selectionActor.width), height: Math.floor(selectionActor.height),
    };
    await Scripting.sleep(150);
    await still(`overlay-${variant}.png`);
    if (recording)
        await frame(600);

    await pressKey(keyboard, Clutter.KEY_Return);
    const editor = await waitUntil(findEditor, 12000, 'editor window');
    await waitUntil(() => findOverlay() === undefined, 5000, 'overlay release');
    await focusEditorWindow(editor, 'editor focus');
    await pressChord(keyboard, Clutter.KEY_Control_L, Clutter.KEY_1);
    await Scripting.sleep(400);
    const editorFrame = windowFrame(editor);
    const origin = canvasOrigin(editorFrame, selection);
    const at = (x, y) => ({x: origin.x + x, y: origin.y + y});
    if (recording) {
        await frame(800);
        await frame(800);
    }

    // Arrow
    let button = toolButton(editorFrame, 'arrow');
    await click(pointer, button.x, button.y);
    await dragRecorded(pointer, at(680, 540), at(520, 400), 5, recording, 90);
    if (recording)
        await frame(300);
    // Rectangle
    button = toolButton(editorFrame, 'rectangle');
    await click(pointer, button.x, button.y);
    // Presses within ~13 px of the canvas edge grab the selection handle, not the tool.
    await dragRecorded(pointer, at(24, 290), at(416, 382), 5, recording, 90);
    if (recording)
        await frame(300);
    // Text
    button = toolButton(editorFrame, 'text');
    await click(pointer, button.x, button.y);
    const anchor = at(740, 480);
    await click(pointer, anchor.x, anchor.y);
    await Scripting.sleep(200);
    await typeText(keyboard, 'Ship');
    if (recording)
        await frame(350);
    await typeText(keyboard, ' it');
    if (recording) {
        await frame(500);
        await frame(500);
    }
    // Pixelate (also commits the text)
    button = toolButton(editorFrame, 'pixelate');
    await click(pointer, button.x, button.y);
    await dragRecorded(pointer, at(1150, 284), at(1492, 384), 4, recording, 90);
    // The proxy-hosted canvas keeps a stale paint of the text widget after the
    // commit; a fit/1:1 round trip re-renders the whole scene.
    await pressChord(keyboard, Clutter.KEY_Control_L, Clutter.KEY_0);
    await pressChord(keyboard, Clutter.KEY_Control_L, Clutter.KEY_1);
    if (recording)
        await frame(500);
    await Scripting.sleep(300);
    await still(`editor-${variant}.png`);
    await screenshotArea(`${OUTPUT_DIR}/editor-crop-${variant}.png`,
        editorFrame.x, editorFrame.y, editorFrame.width, editorFrame.height);

    // Copy closes the editor.
    const copy = copyButton(editorFrame);
    for (let step = 1; step <= 6; step++) {
        const [x, y] = global.get_pointer();
        pointer.notify_absolute_motion(GLib.get_monotonic_time(),
            x + (copy.x - x) / (7 - step), y + (copy.y - y) / (7 - step));
        await Scripting.sleep(30);
    }
    if (recording)
        await frame(700);
    await click(pointer, copy.x, copy.y);
    try {
        await waitUntil(() => findEditor() === null, 5000, 'editor closed by Copy');
    } catch (error) {
        problems.push(`copy button did not close the editor: ${error}`);
        await pressChord(keyboard, Clutter.KEY_Control_L, Clutter.KEY_c);
    }
    await Scripting.sleep(300);
    if (recording) {
        await frame(1500);
        await frame(1500);
    }
    return {selection, editorFrame};
}

async function captureSettings() {
    const before = new Set(global.get_window_actors().map(a => a.meta_window));
    const executable = GLib.getenv('SNIPSNAP_TEST_EXECUTABLE');
    const config = spawn([executable, 'config'], {QT_QPA_PLATFORM: 'wayland'});
    apps.push(config);
    try {
        const window = await waitUntil(() => findWindow(w =>
            !before.has(w) && wmClass(w).includes('snipsnap') &&
            w.get_compositor_private()?.mapped), 15000, 'settings window');
        await Scripting.sleep(800);
        const rect = windowFrame(window);
        await screenshotArea(`${OUTPUT_DIR}/settings.png`,
            rect.x, rect.y, rect.width, rect.height);
    } catch (error) {
        problems.push(`settings window: ${error}`);
    }
    config.force_exit();
}

export async function run() {
    console.log(`SNIPSNAP-E2E-JS-START script=${GLib.getenv('SNIPSNAP_E2E_SCRIPT')} ` +
        `attempt=${GLib.getenv('SNIPSNAP_E2E_ATTEMPT')}`);
    let failure = null;
    try {
        if (!OUTPUT_DIR)
            throw new Error('GNOME_SHELL_E2E_OUTPUT_DIR is unset');
        GLib.mkdir_with_parents(`${OUTPUT_DIR}/flow`, 0o755);
        await Scripting.disableHelperAutoExit();
        await configureMonitors();
        Main.overview.hide();
        await Scripting.waitLeisure();

        const extension = await waitUntil(() => Extension.lookupByUUID(UUID),
            3000, 'enabled SnipSnap extension');
        new Gio.Settings({schema_id: 'org.gnome.shell.keybindings'})
            .set_strv('show-screenshot-ui', []);
        extension.getSettings().set_strv('show-capture-overlay', ['Print']);
        const interfaceSettings = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
        interfaceSettings.set_boolean('enable-hot-corners', false);
        interfaceSettings.set_boolean('clock-show-date', false);
        await setColorScheme(false);
        await startPortalMock();

        const seat = global.stage.context.get_backend().get_default_seat();
        const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);

        writeSnipSnapConfig();
        writeDocument();
        try {
            await launchApp(['gnome-text-editor', `${DEMO_HOME}/Notes/release-notes.md`],
                'texteditor', WINDOWS.editor);
            const calculator = await launchApp(['gnome-calculator'], 'calculator',
                WINDOWS.calculator);
            // A result in the display gives the pixelate patch something to hide.
            Main.activateWindow(calculator);
            await waitUntil(() => global.display.get_focus_window() === calculator,
                3000, 'calculator focus');
            await typeText(keyboard, '4096*3.5');
            await pressKey(keyboard, Clutter.KEY_Return);
            await Scripting.sleep(300);
        } catch (error) {
            problems.push(`GTK apps: ${error}`);
            await Scripting.createTestWindow({width: 960, height: 640, textInput: true});
            await Scripting.waitTestWindows();
        }
        await Scripting.sleep(1500);
        dismissNotifications();

        daemon = spawnDaemon();
        await waitUntil(() => dbusOwnerPid('tech.norvi.snipsnap'), 5000, 'SnipSnap D-Bus owner');
        await Scripting.sleep(500);

        // Park the pointer on the desktop so the idle frame is clean. A fresh
        // virtual pointer sits at (0,0), which is the hot corner, so the shell
        // start-up overview is hidden only after the pointer has left it.
        console.log(`DEMO-POINTER ${JSON.stringify(global.get_pointer())}`);
        await dragRecorded(pointer, {x: 1500, y: 820}, {x: 1500, y: 820}, 1, false);
        await Scripting.sleep(200);
        await hideOverview();
        await Scripting.sleep(300);
        await still('desktop.png');
        await frame(1200);

        const light = await captureFlow(keyboard, pointer, 'light', true);
        console.log(`DEMO-LIGHT ${JSON.stringify(light)}`);
        await captureSettings();

        // Dark variant: same flow, no frames. Qt reads the scheme at start-up.
        try {
            await setColorScheme(true);
            await Scripting.sleep(1500);
            await still('desktop-dark.png');
            daemon.force_exit();
            await waitForProcess(daemon);
            daemon = null;
            await Scripting.sleep(500);
            daemon = spawnDaemon(true);
            await waitUntil(() => dbusOwnerPid('tech.norvi.snipsnap'), 5000, 'SnipSnap D-Bus owner (dark)');
            await Scripting.sleep(500);
            const dark = await captureFlow(keyboard, pointer, 'dark', false);
            console.log(`DEMO-DARK ${JSON.stringify(dark)}`);
        } catch (error) {
            problems.push(`dark variant: ${error}`);
        }

        GLib.file_set_contents(`${OUTPUT_DIR}/flow/frames.json`,
            `${JSON.stringify(frames, null, 1)}\n`);
        console.log(`DEMO-DONE ${JSON.stringify({frames: frames.length, problems})}`);
    } catch (error) {
        failure = error;
        console.error(`DEMO-FAIL ${error.stack ?? error}`);
        console.error(`DEMO-PROBLEMS ${JSON.stringify(problems)}`);
    } finally {
        for (const process of [daemon, portalMock, ...apps]) {
            if (!process)
                continue;
            process.force_exit();
            await waitForProcess(process);
        }
        try {
            await Scripting.destroyTestWindows();
        } catch (_error) {
            // Shell shutdown is allowed to race helper cleanup.
        }
        for (const monitor of testMonitors.reverse())
            monitor.destroy();
        testMonitors = [];
    }
    if (failure)
        throw failure;
}
