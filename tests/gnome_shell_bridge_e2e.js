// SPDX-License-Identifier: GPL-3.0-or-later

import Clutter from 'gi://Clutter';
import GdkPixbuf from 'gi://GdkPixbuf';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import MetaTest from 'gi://MetaTest';
import Shell from 'gi://Shell';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

const UUID = 'snipsnap-shell-bridge@kleos.norvi.tech';
const CAPTURE_ID = 1;
const EDITOR_TITLE = `SnipSnap [capture-id=${CAPTURE_ID}]`;
const EXPECTED_STAGE = {width: 3840, height: 720};
const EXPECTED_MONITOR_LAYOUT = [
    {index: 0, x: 0, y: 0, width: 1280, height: 720, scale: 1},
    {index: 1, x: 1280, y: 0, width: 1280, height: 720, scale: 1},
    {index: 2, x: 2560, y: 0, width: 1280, height: 720, scale: 1},
];

let testMonitors = [];
let daemon = null;

export async function waitUntil(predicate, timeoutMs, label) {
    const deadline = GLib.get_monotonic_time() + timeoutMs * 1000;
    let lastError = null;
    while (GLib.get_monotonic_time() < deadline) {
        try {
            const value = await predicate();
            if (value)
                return value;
        } catch (error) {
            lastError = error;
        }
        await Scripting.sleep(25);
    }
    const suffix = lastError ? `; last error: ${lastError}` : '';
    throw new Error(`timed out waiting for ${label}${suffix}`);
}

export function dbusOwnerPid(name) {
    try {
        const result = Gio.DBus.session.call_sync(
            'org.freedesktop.DBus',
            '/org/freedesktop/DBus',
            'org.freedesktop.DBus',
            'GetConnectionUnixProcessID',
            new GLib.Variant('(s)', [name]),
            new GLib.VariantType('(u)'),
            Gio.DBusCallFlags.NONE,
            250,
            null
        );
        return Number(result.deepUnpack()[0]);
    } catch (_error) {
        return 0;
    }
}

export function spawnSnipSnap(
    traceRunId = 'gnome-shell-bridge-e2e') {
    const executable = GLib.getenv('SNIPSNAP_TEST_EXECUTABLE');
    if (!executable)
        throw new Error('SNIPSNAP_TEST_EXECUTABLE is unset');

    const launcher = new Gio.SubprocessLauncher({
        flags: Gio.SubprocessFlags.NONE,
    });
    for (const [name, value] of Object.entries({
        XDG_SESSION_TYPE: 'wayland',
        XDG_CURRENT_DESKTOP: 'GNOME',
        QT_QPA_PLATFORM: 'wayland',
        WAYLAND_DISPLAY: 'gnome-shell-test-display',
        LIBGL_ALWAYS_SOFTWARE: '1',
        SNIPSNAP_CAPTURE_TRACE: '1',
        SNIPSNAP_CAPTURE_TRACE_RUN_ID: traceRunId,
    })) {
        launcher.setenv(name, value, true);
    }
    return launcher.spawnv([executable]);
}

export function waitForProcess(process) {
    return new Promise(resolve => {
        process.wait_async(null, (source, result) => {
            try {
                source.wait_finish(result);
            } catch (_error) {
                // Cleanup is best effort after all assertions have run.
            }
            resolve();
        });
    });
}

export function windowIdentities(window) {
    const identities = [];
    for (const getter of [
        'get_wm_class',
        'get_wm_class_instance',
        'get_gtk_application_id',
    ]) {
        try {
            const value = window[getter]?.call(window);
            if (value)
                identities.push(value.trim().toLowerCase());
        } catch (_error) {
            // A missing optional identity cannot satisfy the assertion.
        }
    }
    return identities;
}

export function findEditorWindow() {
    for (const actor of global.get_window_actors()) {
        if (actor.meta_window?.get_title() === EDITOR_TITLE)
            return actor.meta_window;
    }
    return null;
}

export function findOverlay() {
    return Main.layoutManager.screenshotUIGroup.get_children().find(actor =>
        actor.get_name?.() === 'snipsnap-shell-bridge-overlay');
}

export function findSelection(overlay) {
    return overlay.get_children().find(actor =>
        actor.has_style_class_name?.('snipsnap-shell-bridge-selection') &&
        actor.visible && actor.width > 1 && actor.height > 1);
}

/**
 * The four shade rectangles must tile the stage exactly around the selection:
 * no gap (an undimmed band) and no overlap (a double-dark band). Both appear
 * when the overlay's layout manager re-aligns a shade instead of honouring the
 * position it was given, which no unit test can see because the geometry the
 * extension computes is correct either way.
 */
export function assertShadeTiling(overlay, selection) {
    const rectangles = overlay.get_children()
        .filter(actor =>
            actor.has_style_class_name?.('snipsnap-shell-bridge-shade'))
        .map(actor => {
            const box = actor.get_allocation_box();
            return {
                x: Math.round(box.x1),
                y: Math.round(box.y1),
                width: Math.round(box.x2 - box.x1),
                height: Math.round(box.y2 - box.y1),
            };
        });

    if (rectangles.length !== 4)
        throw new Error(`expected 4 shade rectangles, found ${rectangles.length}`);

    const overlapping = (left, right) =>
        left.x < right.x + right.width && right.x < left.x + left.width &&
        left.y < right.y + right.height && right.y < left.y + left.height;

    for (let index = 0; index < rectangles.length; index++) {
        if (overlapping(rectangles[index], selection)) {
            throw new Error(
                `shade ${JSON.stringify(rectangles[index])} covers the selection`);
        }
        for (let other = index + 1; other < rectangles.length; other++) {
            if (overlapping(rectangles[index], rectangles[other])) {
                throw new Error('shade rectangles overlap: ' +
                    `${JSON.stringify(rectangles[index])} and ` +
                    `${JSON.stringify(rectangles[other])}`);
            }
        }
    }

    const shaded = rectangles.reduce(
        (total, rectangle) => total + rectangle.width * rectangle.height, 0);
    const expected =
        Math.round(global.stage.width) * Math.round(global.stage.height) -
        selection.width * selection.height;
    if (shaded !== expected) {
        throw new Error(
            `shade covers ${shaded}px of the stage, expected ${expected}px`);
    }
}

export function intersectedMonitorCount(rect, monitors) {
    return monitors.filter(monitor =>
        rect.x < monitor.x + monitor.width &&
        rect.x + rect.width > monitor.x &&
        rect.y < monitor.y + monitor.height &&
        rect.y + rect.height > monitor.y).length;
}

function monitorRectangle(monitor) {
    return {
        index: Number(monitor.index),
        x: Math.floor(monitor.x),
        y: Math.floor(monitor.y),
        width: Math.floor(monitor.width),
        height: Math.floor(monitor.height),
        scale: Number(monitor.geometry_scale),
    };
}

function assertExpectedTopology(monitors) {
    const actual = monitors.map(monitorRectangle);
    if (JSON.stringify(actual) !== JSON.stringify(EXPECTED_MONITOR_LAYOUT)) {
        throw new Error(
            `monitor topology ${JSON.stringify(actual)} != ` +
            JSON.stringify(EXPECTED_MONITOR_LAYOUT));
    }
    const stage = {
        width: Math.floor(global.stage.width),
        height: Math.floor(global.stage.height),
    };
    if (stage.width !== EXPECTED_STAGE.width ||
        stage.height !== EXPECTED_STAGE.height) {
        throw new Error(
            `stage ${stage.width}x${stage.height} != ` +
            `${EXPECTED_STAGE.width}x${EXPECTED_STAGE.height}`);
    }
    return actual;
}

function notifyKey(device, keyval, state) {
    device.notify_keyval(GLib.get_monotonic_time(), keyval, state);
}

export async function pressKey(device, keyval) {
    notifyKey(device, keyval, Clutter.KeyState.PRESSED);
    await Scripting.sleep(40);
    notifyKey(device, keyval, Clutter.KeyState.RELEASED);
    await Scripting.sleep(75);
}

export async function pressChord(device, modifier, keyval) {
    notifyKey(device, modifier, Clutter.KeyState.PRESSED);
    await Scripting.sleep(40);
    notifyKey(device, keyval, Clutter.KeyState.PRESSED);
    await Scripting.sleep(40);
    notifyKey(device, keyval, Clutter.KeyState.RELEASED);
    notifyKey(device, modifier, Clutter.KeyState.RELEASED);
    await Scripting.sleep(100);
}

export async function drag(
    device, start, end, button = Clutter.BUTTON_PRIMARY) {
    const [originX, originY] = global.get_pointer();
    // Mutter may constrain the first absolute event from a fresh virtual
    // pointer to its current output. Walk the pointer to a remote-output start
    // before pressing so reverse cross-monitor drags test the intended origin.
    for (let step = 1; step <= 8; step++) {
        const ratio = step / 8;
        device.notify_absolute_motion(
            GLib.get_monotonic_time(),
            originX + (start.x - originX) * ratio,
            originY + (start.y - originY) * ratio
        );
        await Scripting.sleep(15);
    }
    await Scripting.sleep(50);
    device.notify_button(
        GLib.get_monotonic_time(),
        button,
        Clutter.ButtonState.PRESSED
    );
    await Scripting.sleep(60);

    for (let step = 1; step <= 16; step++) {
        const ratio = step / 16;
        device.notify_absolute_motion(
            GLib.get_monotonic_time(),
            start.x + (end.x - start.x) * ratio,
            start.y + (end.y - start.y) * ratio
        );
        await Scripting.sleep(20);
    }

    device.notify_button(
        GLib.get_monotonic_time(),
        button,
        Clutter.ButtonState.RELEASED
    );
    await Scripting.sleep(100);
}

export async function captureStage(path) {
    const stream = Gio.File.new_for_path(path).replace(
        null,
        false,
        Gio.FileCreateFlags.REPLACE_DESTINATION,
        null
    );
    try {
        await new Shell.Screenshot().screenshot_area(
            0,
            0,
            Math.floor(global.stage.width),
            Math.floor(global.stage.height),
            stream
        );
    } finally {
        stream.close(null);
    }
}

function loadPng(path) {
    const [loaded, bytes] = Gio.File.new_for_path(path).load_contents(null);
    if (!loaded)
        throw new Error(`failed to load compositor screenshot ${path}`);
    const loader = GdkPixbuf.PixbufLoader.new_with_type('png');
    loader.write(bytes);
    loader.close();
    const pixbuf = loader.get_pixbuf();
    if (!pixbuf)
        throw new Error(`failed to decode compositor screenshot ${path}`);
    return pixbuf;
}

function materiallyDifferent(leftPath, rightPath, minimumChangedFraction) {
    const left = loadPng(leftPath);
    const right = loadPng(rightPath);
    if (left.width !== right.width || left.height !== right.height ||
        left.n_channels < 3 || right.n_channels < 3) {
        throw new Error('editor screenshots have incompatible pixel layouts');
    }
    const leftPixels = left.get_pixels();
    const rightPixels = right.get_pixels();
    let compared = 0;
    let changed = 0;
    // Sampling every fourth pixel keeps this state check cheap while still
    // requiring a large, visible canvas transformation rather than cursor or
    // focus noise.
    for (let y = 0; y < left.height; y += 4) {
        for (let x = 0; x < left.width; x += 4) {
            const leftOffset = y * left.rowstride + x * left.n_channels;
            const rightOffset = y * right.rowstride + x * right.n_channels;
            const delta =
                Math.abs(leftPixels[leftOffset] - rightPixels[rightOffset]) +
                Math.abs(leftPixels[leftOffset + 1] - rightPixels[rightOffset + 1]) +
                Math.abs(leftPixels[leftOffset + 2] - rightPixels[rightOffset + 2]);
            compared++;
            if (delta > 72)
                changed++;
        }
    }
    return changed / compared >= minimumChangedFraction;
}

export async function captureUntilMateriallyDifferent(
    referencePath, candidatePath, label, minimumChangedFraction = 0.02) {
    await waitUntil(async () => {
        await captureStage(candidatePath);
        return materiallyDifferent(
            referencePath, candidatePath, minimumChangedFraction);
    }, 3000, label);
}

export async function focusEditorWindow(editor, label) {
    Main.activateWindow(editor);
    await waitUntil(
        () => global.display.get_focus_window() === editor,
        3000,
        label
    );
}

export function windowFrame(window) {
    const frame = window.get_frame_rect();
    return {
        x: Math.floor(frame.x),
        y: Math.floor(frame.y),
        width: Math.floor(frame.width),
        height: Math.floor(frame.height),
    };
}

async function addTestMonitor(expectedCount) {
    const changed = new Promise(resolve => {
        const signalId = Main.layoutManager.connect('monitors-changed', () => {
            Main.layoutManager.disconnect(signalId);
            resolve();
        });
    });
    testMonitors.push(
        MetaTest.TestMonitor.new(global.context, 1280, 720, 60.0));
    await changed;
    await waitUntil(
        () => Main.layoutManager.monitors.length === expectedCount,
        3000,
        `${expectedCount} monitors`
    );
}

export async function run() {
    console.log(
        `SNIPSNAP-E2E-JS-START script=${GLib.getenv('SNIPSNAP_E2E_SCRIPT')} ` +
        `attempt=${GLib.getenv('SNIPSNAP_E2E_ATTEMPT')}`
    );
    let failure = null;
    try {
        await Scripting.disableHelperAutoExit();
        if (Main.layoutManager.monitors.length !== 1)
            throw new Error('test tool did not start with exactly one monitor');
        await addTestMonitor(2);
        await addTestMonitor(3);

        await Scripting.createTestWindow({
            width: 720,
            height: 420,
            textInput: false,
        });
        await Scripting.waitTestWindows();
        Main.overview.hide();
        await Scripting.waitLeisure();

        const extension = await waitUntil(
            () => Extension.lookupByUUID(UUID),
            3000,
            'enabled SnipSnap extension'
        );
        new Gio.Settings({
            schema_id: 'org.gnome.shell.keybindings',
        }).set_strv('show-screenshot-ui', []);
        extension.getSettings().set_strv('show-capture-overlay', ['Print']);
        await Scripting.sleep(250);

        daemon = spawnSnipSnap();
        const spawnedPid = Number(daemon.get_identifier());
        const daemonPid = await waitUntil(
            () => dbusOwnerPid('tech.norvi.snipsnap'),
            5000,
            'SnipSnap D-Bus owner'
        );
        if (daemonPid !== spawnedPid)
            throw new Error(`D-Bus owner PID ${daemonPid} != daemon PID ${spawnedPid}`);

        const socketPath = GLib.build_filenamev([
            GLib.get_user_runtime_dir(),
            'snipsnap',
            'gnome-shell-bridge-v1.sock',
        ]);
        await waitUntil(
            () => GLib.file_test(socketPath, GLib.FileTest.EXISTS),
            3000,
            'SnipSnap bridge socket'
        );

        const monitors = [...Main.layoutManager.monitors]
            .sort((left, right) => left.x - right.x || left.y - right.y);
        if (monitors.length !== 3)
            throw new Error(`expected 3 monitors, found ${monitors.length}`);
        const monitorLayout = assertExpectedTopology(monitors);
        const first = monitors[0];
        const last = monitors[2];
        // Finish the reverse drag on the primary output. Together with the
        // mixed test (which finishes on the rightmost output), this exercises
        // both a panel-constrained primary work area and an unconstrained
        // secondary output for the compositor-bounded editor host.
        const start = {
            x: last.x + last.width - 64,
            y: last.y + Math.min(last.height - 120, 520),
        };
        const end = {
            x: first.x + 64,
            y: first.y + 140,
        };

        const seat = global.stage.context.get_backend().get_default_seat();
        const keyboard = seat.create_virtual_device(
            Clutter.InputDeviceType.KEYBOARD_DEVICE);
        const pointer = seat.create_virtual_device(
            Clutter.InputDeviceType.POINTER_DEVICE);

        await pressKey(keyboard, Clutter.KEY_Print);
        const overlay = await waitUntil(
            findOverlay,
            5000,
            'all-monitor capture overlay'
        );
        if (overlay.width !== global.stage.width ||
            overlay.height !== global.stage.height) {
            throw new Error(
                `overlay ${overlay.width}x${overlay.height} does not cover stage ` +
                `${global.stage.width}x${global.stage.height}`);
        }

        await drag(pointer, start, end);
        const selectionActor = await waitUntil(
            () => findSelection(overlay),
            3000,
            'cross-monitor selection'
        );
        const selection = {
            x: Math.floor(selectionActor.x),
            y: Math.floor(selectionActor.y),
            width: Math.floor(selectionActor.width),
            height: Math.floor(selectionActor.height),
        };
        const spannedMonitors = intersectedMonitorCount(selection, monitors);
        if (spannedMonitors !== 3)
            throw new Error(`selection intersects ${spannedMonitors}, not 3, monitors`);
        assertShadeTiling(overlay, selection);

        const outputDirectory = GLib.getenv('GNOME_SHELL_E2E_OUTPUT_DIR');
        if (!outputDirectory)
            throw new Error('GNOME_SHELL_E2E_OUTPUT_DIR is unset');
        const overlayScreenshot = GLib.build_filenamev([
            outputDirectory, 'overlay.png',
        ]);
        const editorScreenshot = GLib.build_filenamev([
            outputDirectory, 'editor.png',
        ]);
        const oneToOneScreenshot = GLib.build_filenamev([
            outputDirectory, 'editor-one-to-one.png',
        ]);
        await captureStage(overlayScreenshot);

        await pressKey(keyboard, Clutter.KEY_Return);
        const editor = await waitUntil(
            findEditorWindow,
            12_000,
            'exact SnipSnap bridge editor window'
        );
        await waitUntil(
            () => findOverlay() === undefined,
            5000,
            'overlay release after commit'
        );
        await waitUntil(
            () => global.display.get_focus_window() === editor,
            3000,
            'exact SnipSnap editor focus'
        );
        // Bind the proof to the completed focus wait. A later compositor
        // screenshot may transiently focus test infrastructure on some
        // runners; that does not erase the editor activation we observed.
        const editorFocused = global.display.get_focus_window() === editor;

        const identities = windowIdentities(editor);
        const editorPid = Number(editor.get_pid());
        const editorNormal = editor.get_window_type() === Meta.WindowType.NORMAL;
        const editorShowing = editor.showing_on_its_workspace();
        const editorMapped = editor.get_compositor_private()?.mapped === true;
        if (editorPid !== daemonPid)
            throw new Error(`editor PID ${editorPid} != daemon PID ${daemonPid}`);
        if (!editorNormal)
            throw new Error(`editor is not NORMAL: ${editor.get_window_type()}`);
        if (!editorShowing || !editorMapped)
            throw new Error('editor is not mapped and showing on its workspace');
        if (!identities.some(value =>
            value === 'snipsnap' || value === 'tech.norvi.snipsnap')) {
            throw new Error(`editor identity rejected: ${identities}`);
        }

        await Scripting.sleep(250);
        await captureStage(editorScreenshot);
        await focusEditorWindow(editor, 'editor focus before 1:1 shortcut');
        await pressChord(keyboard, Clutter.KEY_Control_L, Clutter.KEY_1);
        await captureUntilMateriallyDifferent(
            editorScreenshot,
            oneToOneScreenshot,
            'observable 1:1 editor view'
        );
        await focusEditorWindow(editor, 'editor focus before pan');
        const editorFrame = windowFrame(editor);
        if (editorFrame.x !== first.x ||
            editorFrame.width !== first.width ||
            editorFrame.y <= first.y ||
            editorFrame.height >= first.height ||
            editorFrame.y + editorFrame.height !== first.y + first.height) {
            throw new Error(
                `primary work-area editor frame ${JSON.stringify(editorFrame)} ` +
                `does not match monitor ${JSON.stringify(monitorRectangle(first))}`);
        }
        await drag(
            pointer,
            {x: editorFrame.x + editorFrame.width / 2,
             y: editorFrame.y + editorFrame.height / 2},
            {x: editorFrame.x + editorFrame.width / 2 - 120,
             y: editorFrame.y + editorFrame.height / 2},
            Clutter.BUTTON_MIDDLE
        );
        await focusEditorWindow(editor, 'editor focus before fit shortcut');
        await pressChord(keyboard, Clutter.KEY_Control_L, Clutter.KEY_0);
        console.log(`E2E-PASS ${JSON.stringify({
            event: 'passed',
            capture_id: CAPTURE_ID,
            daemon_pid: daemonPid,
            monitors: monitors.length,
            stage_width: Math.floor(global.stage.width),
            stage_height: Math.floor(global.stage.height),
            monitor_layout: monitorLayout,
            selection,
            selection_spans_monitors: spannedMonitors,
            editor_normal: editorNormal,
            editor_focused: editorFocused,
            editor_showing: editorShowing,
            editor_mapped: editorMapped,
            overlay_closed: findOverlay() === undefined,
            editor_title: editor.get_title(),
            identities,
            overlay_screenshot: overlayScreenshot,
            editor_screenshot: editorScreenshot,
            editor_one_to_one_screenshot: oneToOneScreenshot,
            editor_frame: editorFrame,
        })}`);
    } catch (error) {
        failure = error;
        console.error(`E2E-FAIL ${error.stack ?? error}`);
    } finally {
        if (daemon) {
            daemon.force_exit();
            await waitForProcess(daemon);
            daemon = null;
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
