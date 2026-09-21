// SPDX-License-Identifier: GPL-3.0-or-later

import Clutter from 'gi://Clutter';
import GdkPixbuf from 'gi://GdkPixbuf';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import MetaTest from 'gi://MetaTest';
import Shell from 'gi://Shell';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

import {
    captureStage,
    captureUntilMateriallyDifferent,
    dbusOwnerPid,
    drag,
    findEditorWindow,
    findOverlay,
    findSelection,
    focusEditorWindow,
    intersectedMonitorCount,
    pressChord,
    pressKey,
    spawnSnipSnap,
    waitForProcess,
    waitUntil,
    windowFrame,
    windowIdentities,
} from './gnome_shell_bridge_e2e.js';

const UUID = 'snipsnap-shell-bridge@kleos.norvi.tech';
const CAPTURE_ID = 1;
const DISPLAY_CONFIG_NAME = 'org.gnome.Mutter.DisplayConfig';
const DISPLAY_CONFIG_PATH = '/org/gnome/Mutter/DisplayConfig';
const EXPECTED_STAGE = {width: 2560, height: 800};
const EXPECTED_PHYSICAL_STAGE = {width: 3640, height: 1600};
const EXPECTED_LOGICAL_LAYOUT = [
    {x: 0, y: 0, width: 480, height: 800, scale: 1},
    {x: 480, y: 160, width: 800, height: 480, scale: 1.25},
    {x: 1280, y: 160, width: 800, height: 480, scale: 1.5},
    {x: 2080, y: 0, width: 480, height: 800, scale: 2},
];
const EXPECTED_PHYSICAL_LAYOUT = [
    {x: 0, y: 0, width: 480, height: 800, scale: 1},
    {x: 480, y: 100, width: 1000, height: 600, scale: 1},
    {x: 1480, y: 40, width: 1200, height: 720, scale: 1},
    {x: 2680, y: 0, width: 960, height: 1600, scale: 2},
];
const MONITOR_SPECS = [
    {modeWidth: 800, modeHeight: 480, x: 0, y: 0, scale: 1, transform: 3},
    {modeWidth: 1000, modeHeight: 600, x: 480, y: 160, scale: 1.25, transform: 0},
    {modeWidth: 1200, modeHeight: 720, x: 1280, y: 160, scale: 1.5, transform: 0},
    {modeWidth: 1600, modeHeight: 960, x: 2080, y: 0, scale: 2, transform: 1},
];
const PHYSICAL_MONITOR_SPECS = [
    {modeWidth: 800, modeHeight: 480, x: 0, y: 0, scale: 1, transform: 3},
    {modeWidth: 1000, modeHeight: 600, x: 480, y: 100, scale: 1, transform: 0},
    {modeWidth: 1200, modeHeight: 720, x: 1480, y: 40, scale: 1, transform: 0},
    {modeWidth: 1600, modeHeight: 960, x: 2680, y: 0, scale: 2, transform: 1},
];
const MARKERS = [
    {x: 80, y: 200, color: '#ff0000', expected: [255, 0, 0]},
    {x: 600, y: 240, color: '#00ff00', expected: [0, 255, 0]},
    {x: 1400, y: 500, color: '#0000ff', expected: [0, 0, 255]},
    {x: 2300, y: 600, color: '#ffff00', expected: [255, 255, 0]},
];
const PHYSICAL_MARKERS = [
    {x: 40, y: 60, color: '#ff0000', expected: [255, 0, 0]},
    {x: 560, y: 260, color: '#00ff00', expected: [0, 255, 0]},
    {x: 1600, y: 340, color: '#0000ff', expected: [0, 0, 255]},
    {x: 3000, y: 260, color: '#ffff00', expected: [255, 255, 0]},
];

let testMonitors = [];
let daemon = null;
let landmarkActor = null;

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

function displayConfigCall(method, parameters = null) {
    return new Promise((resolve, reject) => {
        Gio.DBus.session.call(
            DISPLAY_CONFIG_NAME,
            DISPLAY_CONFIG_PATH,
            DISPLAY_CONFIG_NAME,
            method,
            parameters,
            null,
            Gio.DBusCallFlags.NO_AUTO_START,
            5000,
            null,
            (connection, result) => {
                try {
                    resolve(connection.call_finish(result));
                } catch (error) {
                    reject(error);
                }
            }
        );
    });
}

async function addTestMonitor(width, height, expectedCount) {
    const changed = new Promise(resolve => {
        const signalId = Main.layoutManager.connect('monitors-changed', () => {
            Main.layoutManager.disconnect(signalId);
            resolve();
        });
    });
    testMonitors.push(
        MetaTest.TestMonitor.new(global.context, width, height, 60.0));
    await changed;
    await waitUntil(
        () => Main.layoutManager.monitors.length === expectedCount,
        3000,
        `${expectedCount} pre-configuration monitors`
    );
}

function findPhysicalMonitor(physicalMonitors, specification) {
    const matches = [];
    for (const monitor of physicalMonitors) {
        const [identity, modes] = monitor;
        for (const mode of modes) {
            if (Number(mode[1]) === specification.modeWidth &&
                Number(mode[2]) === specification.modeHeight) {
                matches.push({
                    connector: String(identity[0]),
                    modeId: String(mode[0]),
                    supportedScales: mode[5].map(Number),
                });
            }
        }
    }
    if (matches.length !== 1) {
        throw new Error(
            `mode ${specification.modeWidth}x${specification.modeHeight} ` +
            `matched ${matches.length} physical monitors`);
    }
    if (!matches[0].supportedScales.some(scale =>
        Math.abs(scale - specification.scale) < 0.0001)) {
        throw new Error(
            `scale ${specification.scale} is unavailable for ` +
            `${specification.modeWidth}x${specification.modeHeight}`);
    }
    return matches[0];
}

async function configureTestMonitors() {
    await addTestMonitor(800, 480, 2);
    await addTestMonitor(1000, 600, 3);
    await addTestMonitor(1200, 720, 4);
    await addTestMonitor(1600, 960, 5);

    const state = unpackVariants(
        await displayConfigCall('GetCurrentState'));
    const physicalMonitors = state[1];
    return MONITOR_SPECS.map(specification => ({
        ...specification,
        ...findPhysicalMonitor(physicalMonitors, specification),
    }));
}

async function applyTopology(selected, specifications, layoutMode,
    assertion, label) {
    const state = unpackVariants(
        await displayConfigCall('GetCurrentState'));
    const logicalMonitors = selected.map((monitor, index) => [
        specifications[index].x,
        specifications[index].y,
        specifications[index].scale,
        specifications[index].transform,
        index === 0,
        [[monitor.connector, monitor.modeId, {}]],
    ]);
    const parameters = new GLib.Variant(
        '(uua(iiduba(ssa{sv}))a{sv})',
        [
            Number(state[0]),
            1,
            logicalMonitors,
            {'layout-mode': new GLib.Variant('u', layoutMode)},
        ]
    );
    await displayConfigCall('ApplyMonitorsConfig', parameters);
    await waitUntil(
        () => {
            try {
                assertion(Main.layoutManager.monitors);
                return true;
            } catch (_error) {
                return false;
            }
        },
        5000,
        label
    );
    await Scripting.waitLeisure();
}

async function applyPhysicalTopology(selected) {
    await applyTopology(
        selected,
        PHYSICAL_MONITOR_SPECS,
        2,
        assertPhysicalTopology,
        'mixed-scale rotated physical topology'
    );
}

async function applyMixedTopology(selected) {
    await applyTopology(
        selected,
        MONITOR_SPECS,
        1,
        assertMixedTopology,
        'mixed-scale rotated logical topology'
    );
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

function stageViewScales() {
    return global.stage.peek_stage_views()
        .map(view => Number(view.get_scale()))
        .sort((left, right) => left - right);
}

function assertStageViewScales(expected) {
    const actual = stageViewScales();
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
        throw new Error(
            `stage view scales ${JSON.stringify(actual)} != ` +
            JSON.stringify(expected));
    }
}

function assertTopology(sourceMonitors, expectedLayout, expectedStage,
    expectedStageViewScales, label) {
    const monitors = [...sourceMonitors]
        .sort((left, right) => left.x - right.x || left.y - right.y);
    const actual = monitors.map(monitor => {
        const rectangle = monitorRectangle(monitor);
        return {
            x: rectangle.x,
            y: rectangle.y,
            width: rectangle.width,
            height: rectangle.height,
            scale: rectangle.scale,
        };
    });
    if (JSON.stringify(actual) !== JSON.stringify(expectedLayout)) {
        throw new Error(
            `${label} topology ${JSON.stringify(actual)} != ` +
            JSON.stringify(expectedLayout));
    }
    if (Math.floor(global.stage.width) !== expectedStage.width ||
        Math.floor(global.stage.height) !== expectedStage.height) {
        throw new Error(
            `${label} stage ${global.stage.width}x${global.stage.height} != ` +
            `${expectedStage.width}x${expectedStage.height}`);
    }
    assertStageViewScales(expectedStageViewScales);
    return monitors;
}

function assertMixedTopology(sourceMonitors) {
    return assertTopology(
        sourceMonitors,
        EXPECTED_LOGICAL_LAYOUT,
        EXPECTED_STAGE,
        [1, 1.25, 1.5, 2],
        'mixed logical'
    );
}

function assertPhysicalTopology(sourceMonitors) {
    return assertTopology(
        sourceMonitors,
        EXPECTED_PHYSICAL_LAYOUT,
        EXPECTED_PHYSICAL_STAGE,
        [1, 1, 1, 1],
        'mixed physical'
    );
}

function waitForStagePaint() {
    return new Promise(resolve => {
        const signalId = global.stage.connect('after-paint', () => {
            global.stage.disconnect(signalId);
            resolve();
        });
        global.stage.queue_redraw();
    });
}

function createLandmarks(stageSize, markers) {
    const actor = new St.Widget({
        layout_manager: new Clutter.FixedLayout(),
        reactive: false,
        style: 'background-color: #000000;',
    });
    actor.set_position(0, 0);
    actor.set_size(stageSize.width, stageSize.height);
    for (const marker of markers) {
        const child = new St.Widget({
            reactive: false,
            style: `background-color: ${marker.color};`,
        });
        child.set_position(marker.x, marker.y);
        child.set_size(48, 48);
        actor.add_child(child);
    }
    Main.uiGroup.add_child(actor);
    return actor;
}

function writeBytes(path, bytes) {
    const stream = Gio.File.new_for_path(path).replace(
        null,
        false,
        Gio.FileCreateFlags.REPLACE_DESTINATION,
        null
    );
    try {
        stream.write_all(bytes, null);
    } finally {
        stream.close(null);
    }
}

function decodePng(bytes) {
    const loader = GdkPixbuf.PixbufLoader.new_with_type('png');
    loader.write(bytes);
    loader.close();
    const pixbuf = loader.get_pixbuf();
    if (!pixbuf)
        throw new Error('mixed topology PNG did not decode');
    return pixbuf;
}

function samplePixel(pixbuf, x, y) {
    if (x < 0 || y < 0 || x >= pixbuf.width || y >= pixbuf.height)
        throw new Error(`sample ${x},${y} is outside encoded PNG`);
    const channels = pixbuf.n_channels;
    const offset = y * pixbuf.rowstride + x * channels;
    const pixels = pixbuf.get_pixels();
    return [pixels[offset], pixels[offset + 1], pixels[offset + 2]];
}

function assertColor(actual, expected, label) {
    for (let channel = 0; channel < 3; channel++) {
        if (Math.abs(actual[channel] - expected[channel]) > 24) {
            throw new Error(
                `${label} pixel ${actual} differs from ${expected}`);
        }
    }
}

function assertEditorLandmarks(path, frame, selection, markers, gap) {
    const [loaded, bytes] = Gio.File.new_for_path(path).load_contents(null);
    if (!loaded)
        throw new Error(`failed to load editor screenshot ${path}`);
    const pixbuf = decodePng(bytes);
    const screenshotScaleX = pixbuf.width / EXPECTED_STAGE.width;
    const screenshotScaleY = pixbuf.height / EXPECTED_STAGE.height;
    if (screenshotScaleX <= 0 ||
        Math.abs(screenshotScaleX - screenshotScaleY) > 0.0001) {
        throw new Error(
            `editor screenshot scale ${screenshotScaleX}x${screenshotScaleY} ` +
            'is not uniform');
    }

    const viewScale = Math.min(
        frame.width / selection.width,
        frame.height / selection.height,
        1
    );
    const contentX = frame.x + (frame.width - selection.width * viewScale) / 2;
    const contentY = frame.y + (frame.height - selection.height * viewScale) / 2;
    const sampleCanvasPoint = (stageX, stageY, expected, label) => {
        const canvasX = stageX - selection.x;
        const canvasY = stageY - selection.y;
        if (canvasX < 0 || canvasY < 0 ||
            canvasX >= selection.width || canvasY >= selection.height) {
            throw new Error(`${label} is outside the editor selection`);
        }
        const pixelX = Math.floor(
            (contentX + canvasX * viewScale) * screenshotScaleX);
        const pixelY = Math.floor(
            (contentY + canvasY * viewScale) * screenshotScaleY);
        assertColor(samplePixel(pixbuf, pixelX, pixelY), expected, label);
    };

    for (const [index, marker] of markers.entries()) {
        sampleCanvasPoint(
            marker.x + 24,
            marker.y + 24,
            marker.expected,
            `editor monitor ${index} landmark`
        );
    }
    sampleCanvasPoint(gap.x, gap.y, [0, 0, 0], 'editor stage hole');
}

function assertOneToOneLandmark(path, frame, selection, marker) {
    const [loaded, bytes] = Gio.File.new_for_path(path).load_contents(null);
    if (!loaded)
        throw new Error(`failed to load 1:1 editor screenshot ${path}`);
    const pixbuf = decodePng(bytes);
    const screenshotScaleX = pixbuf.width / EXPECTED_STAGE.width;
    const screenshotScaleY = pixbuf.height / EXPECTED_STAGE.height;
    if (Math.abs(screenshotScaleX - screenshotScaleY) > 0.0001)
        throw new Error('1:1 editor screenshot scale is not uniform');

    const canvasX = marker.x + 24 - selection.x;
    const canvasY = marker.y + 24 - selection.y;
    const stageX = frame.x + frame.width / 2 +
        canvasX - selection.width / 2;
    const stageY = frame.y + frame.height / 2 +
        canvasY - selection.height / 2;
    assertColor(
        samplePixel(
            pixbuf,
            Math.floor(stageX * screenshotScaleX),
            Math.floor(stageY * screenshotScaleY)
        ),
        marker.expected,
        '1:1 editor center landmark'
    );
}

async function proveProductionEncoder(extension, monitors, outputDirectory, {
    proofMarker,
    pngName,
    expectedStage,
    expectedScale,
    expectedLayoutMode,
    selection,
    expectedPixelWidth,
    expectedPixelHeight,
    markers,
    gap,
    retainLandmarks = false,
}) {
    landmarkActor = createLandmarks(expectedStage, markers);
    await waitForStagePaint();

    const topologyModule = await import(
        extension.dir.get_child('capture-topology.js').get_uri());
    const selectedRegionModule = await import(
        extension.dir.get_child('selected-region.js').get_uri());
    const [content, scale] =
        await new Shell.Screenshot().screenshot_stage_to_content();
    const topology = topologyModule.snapshotCaptureTopology({
        width: Math.floor(global.stage.width),
        height: Math.floor(global.stage.height),
    }, Main.layoutManager.monitors, 1, stageViewScales());
    const encoded = await selectedRegionModule.encodeSelectedRegion(
        content, selection, scale, topology);
    const texture = content.get_texture();
    if (topology.layoutMode !== expectedLayoutMode ||
        scale !== expectedScale ||
        texture.get_width() !== expectedStage.width * expectedScale ||
        texture.get_height() !== expectedStage.height * expectedScale ||
        encoded.pixelRegion.width !== expectedPixelWidth ||
        encoded.pixelRegion.height !== expectedPixelHeight) {
        throw new Error(
            `${expectedLayoutMode} encoder returned mode ${topology.layoutMode}, ` +
            `scale ${scale}, texture ${texture.get_width()}x` +
            `${texture.get_height()}, region ${encoded.pixelRegion.width}x` +
            encoded.pixelRegion.height);
    }

    const pngPath = GLib.build_filenamev([
        outputDirectory, pngName,
    ]);
    writeBytes(pngPath, encoded.png);
    const pixbuf = decodePng(encoded.png);
    for (const [index, marker] of markers.entries()) {
        const logicalX = marker.x + 24;
        const logicalY = marker.y + 24;
        const pixelX = Math.floor(logicalX * scale) - encoded.pixelRegion.x;
        const pixelY = Math.floor(logicalY * scale) - encoded.pixelRegion.y;
        assertColor(
            samplePixel(pixbuf, pixelX, pixelY),
            marker.expected,
            `monitor ${index} landmark`
        );
    }
    const gapX = Math.floor(gap.x * scale) - encoded.pixelRegion.x;
    const gapY = Math.floor(gap.y * scale) - encoded.pixelRegion.y;
    assertColor(samplePixel(pixbuf, gapX, gapY), [0, 0, 0], 'stage hole');

    const proof = {
        topology_id: topology.id,
        monitor_layout: monitors.map(monitorRectangle),
        stage_view_scales: [...topology.stageViewScales],
        capture_layout_mode: topology.layoutMode,
        capture_scale: scale,
        stage_width: topology.stageWidth,
        stage_height: topology.stageHeight,
        texture_width: texture.get_width(),
        texture_height: texture.get_height(),
        selection,
        pixel_width: encoded.pixelRegion.width,
        pixel_height: encoded.pixelRegion.height,
        png_path: pngPath,
        landmarks: markers.length,
        stage_hole_verified: true,
    };
    console.log(`${proofMarker} ${JSON.stringify(proof)}`);
    if (!retainLandmarks) {
        landmarkActor.destroy();
        landmarkActor = null;
        await waitForStagePaint();
    }
    return proof;
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
        const selectedMonitors = await configureTestMonitors();
        await applyPhysicalTopology(selectedMonitors);
        const physicalMonitors = assertPhysicalTopology(
            Main.layoutManager.monitors);

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

        const outputDirectory = GLib.getenv('GNOME_SHELL_E2E_OUTPUT_DIR');
        if (!outputDirectory)
            throw new Error('GNOME_SHELL_E2E_OUTPUT_DIR is unset');
        const physicalEncoderProof = await proveProductionEncoder(
            extension,
            physicalMonitors,
            outputDirectory,
            {
                proofMarker: 'PHYSICAL-ENCODER-PASS',
                pngName: 'physical-selected.png',
                expectedStage: EXPECTED_PHYSICAL_STAGE,
                expectedScale: 1,
                expectedLayoutMode: 'physical',
                selection: {x: 20, y: 20, width: 3600, height: 1560},
                expectedPixelWidth: 3600,
                expectedPixelHeight: 1560,
                markers: PHYSICAL_MARKERS,
                gap: {x: 800, y: 50},
            }
        );

        await applyMixedTopology(selectedMonitors);
        const monitors = assertMixedTopology(Main.layoutManager.monitors);
        const encoderProof = await proveProductionEncoder(
            extension,
            monitors,
            outputDirectory,
            {
                proofMarker: 'MIXED-ENCODER-PASS',
                pngName: 'mixed-selected.png',
                expectedStage: EXPECTED_STAGE,
                expectedScale: 2,
                expectedLayoutMode: 'logical',
                selection: {x: 20, y: 20, width: 2520, height: 760},
                expectedPixelWidth: 5040,
                expectedPixelHeight: 1520,
                markers: MARKERS,
                gap: {x: 1000, y: 80},
                retainLandmarks: true,
            }
        );

        await Scripting.createTestWindow({
            width: 320,
            height: 240,
            textInput: false,
        });
        await Scripting.waitTestWindows();
        Main.overview.hide();
        await Scripting.waitLeisure();

        daemon = spawnSnipSnap('gnome-shell-bridge-mixed-e2e');
        const spawnedPid = Number(daemon.get_identifier());
        const daemonPid = await waitUntil(
            () => dbusOwnerPid('tech.norvi.snipsnap'),
            5000,
            'SnipSnap D-Bus owner'
        );
        if (daemonPid !== spawnedPid)
            throw new Error(`D-Bus owner PID ${daemonPid} != daemon PID ${spawnedPid}`);
        await waitUntil(
            () => GLib.file_test(GLib.build_filenamev([
                GLib.get_user_runtime_dir(),
                'snipsnap',
                'gnome-shell-bridge-v1.sock',
            ]), GLib.FileTest.EXISTS),
            3000,
            'SnipSnap bridge socket'
        );

        const seat = global.stage.context.get_backend().get_default_seat();
        const keyboard = seat.create_virtual_device(
            Clutter.InputDeviceType.KEYBOARD_DEVICE);
        const pointer = seat.create_virtual_device(
            Clutter.InputDeviceType.POINTER_DEVICE);
        await pressKey(keyboard, Clutter.KEY_Print);
        const overlay = await waitUntil(
            findOverlay, 5000, 'mixed-topology capture overlay');
        if (overlay.width !== EXPECTED_STAGE.width ||
            overlay.height !== EXPECTED_STAGE.height) {
            throw new Error(
                `overlay ${overlay.width}x${overlay.height} does not cover ` +
                `${EXPECTED_STAGE.width}x${EXPECTED_STAGE.height}`);
        }

        await drag(pointer, {x: 20, y: 20}, {x: 2540, y: 780});
        const selectionActor = await waitUntil(
            () => findSelection(overlay), 3000, 'mixed cross-monitor selection');
        const selection = {
            x: Math.floor(selectionActor.x),
            y: Math.floor(selectionActor.y),
            width: Math.floor(selectionActor.width),
            height: Math.floor(selectionActor.height),
        };
        const spannedMonitors = intersectedMonitorCount(selection, monitors);
        if (spannedMonitors !== 4)
            throw new Error(`selection intersects ${spannedMonitors}, not 4, monitors`);
        if (selection.width >= EXPECTED_STAGE.width ||
            selection.height >= EXPECTED_STAGE.height ||
            selection.x + selection.width > EXPECTED_STAGE.width ||
            selection.y + selection.height > EXPECTED_STAGE.height) {
            throw new Error(
                `selection ${JSON.stringify(selection)} is not a bounded ` +
                'partial-stage rectangle');
        }

        const overlayScreenshot = GLib.build_filenamev([
            outputDirectory, 'mixed-overlay.png',
        ]);
        const editorScreenshot = GLib.build_filenamev([
            outputDirectory, 'mixed-editor.png',
        ]);
        const oneToOneScreenshot = GLib.build_filenamev([
            outputDirectory, 'mixed-editor-one-to-one.png',
        ]);
        await captureStage(overlayScreenshot);
        // The overlay owns an immutable snapshot containing these landmarks.
        // Remove the live actors before committing so only the captured image,
        // not test chrome painted over the editor, can satisfy the proof.
        landmarkActor.destroy();
        landmarkActor = null;
        await waitForStagePaint();
        await pressKey(keyboard, Clutter.KEY_Return);
        const editor = await waitUntil(
            findEditorWindow, 12_000, 'mixed-topology SnipSnap editor');
        await waitUntil(
            () => findOverlay() === undefined,
            5000,
            'mixed overlay release after commit'
        );
        await waitUntil(
            () => global.display.get_focus_window() === editor,
            3000,
            'mixed-topology editor focus'
        );
        const editorFocused = global.display.get_focus_window() === editor;

        const identities = windowIdentities(editor);
        const editorPid = Number(editor.get_pid());
        const editorNormal = editor.get_window_type() === Meta.WindowType.NORMAL;
        const editorShowing = editor.showing_on_its_workspace();
        const editorMapped = editor.get_compositor_private()?.mapped === true;
        if (editorPid !== daemonPid || !editorNormal ||
            !editorShowing || !editorMapped) {
            throw new Error('mixed-topology editor identity or mapping failed');
        }
        if (!identities.some(value =>
            value === 'snipsnap' || value === 'tech.norvi.snipsnap')) {
            throw new Error(`editor identity rejected: ${identities}`);
        }

        // The helper window is intentionally present through mapping and focus
        // so this run retains the Mutter stack-race regression. Remove it only
        // after that proof: test-tool helper windows may be kept above a normal
        // client even when the editor owns keyboard focus, which can occlude
        // the captured landmark pixels in the compositor screenshot.
        await Scripting.destroyTestWindows();
        await waitForStagePaint();
        await focusEditorWindow(editor, 'mixed editor focus after helper removal');
        await Scripting.sleep(250);
        await captureStage(editorScreenshot);
        const editorFrame = windowFrame(editor);
        assertEditorLandmarks(
            editorScreenshot,
            editorFrame,
            selection,
            MARKERS,
            {x: 1000, y: 80}
        );
        await focusEditorWindow(
            editor, 'mixed editor focus before 1:1 shortcut');
        await pressChord(keyboard, Clutter.KEY_Control_L, Clutter.KEY_1);
        await captureUntilMateriallyDifferent(
            editorScreenshot,
            oneToOneScreenshot,
            'observable mixed 1:1 editor view',
            0.001
        );
        assertOneToOneLandmark(
            oneToOneScreenshot, editorFrame, selection, MARKERS[2]);
        await focusEditorWindow(editor, 'mixed editor focus before pan');
        await drag(
            pointer,
            {x: editorFrame.x + editorFrame.width / 2,
             y: editorFrame.y + editorFrame.height / 2},
            {x: editorFrame.x + editorFrame.width / 2 - 80,
             y: editorFrame.y + editorFrame.height / 2 - 60},
            Clutter.BUTTON_MIDDLE
        );
        await focusEditorWindow(editor, 'mixed editor focus before fit shortcut');
        await pressChord(keyboard, Clutter.KEY_Control_L, Clutter.KEY_0);
        console.log(`MIXED-E2E-PASS ${JSON.stringify({
            event: 'passed',
            capture_id: CAPTURE_ID,
            daemon_pid: daemonPid,
            monitors: monitors.length,
            stage_width: EXPECTED_STAGE.width,
            stage_height: EXPECTED_STAGE.height,
            monitor_layout: monitors.map(monitorRectangle),
            selection,
            selection_spans_monitors: spannedMonitors,
            physical_encoder_proof: physicalEncoderProof,
            encoder_proof: encoderProof,
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
            helper_window_removed: true,
            editor_landmarks_verified: true,
        })}`);
    } catch (error) {
        failure = error;
        console.error(`MIXED-E2E-FAIL ${error.stack ?? error}`);
    } finally {
        landmarkActor?.destroy();
        landmarkActor = null;
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
