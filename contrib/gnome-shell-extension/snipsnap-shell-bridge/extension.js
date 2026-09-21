// SPDX-License-Identifier: GPL-3.0-or-later

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {
    captureTopologiesEqual,
    snapshotCaptureTopology,
    topologyLogFields,
    validateCapturedStage,
    validateStageCaptureBudget,
} from './capture-topology.js';
import {
    CaptureLifecycle,
    unlockedActionModes,
} from './capture-lifecycle.js';
import {
    formatDimensions,
    labelPosition,
    selectionFromPoints,
    shadeRectangles,
} from './geometry.js';
import {handoffFrame} from './handoff-client.js';
import {AckStatus, encodeRequest} from './handoff-protocol.js';
import {
    HandoffState,
    UncancellableWorkGate,
    editorWindowTitle,
    matchesEditorWindow,
} from './handoff-state.js';
import {encodeSelectedRegion} from './selected-region.js';
import {overlayAccentStyles} from './accent-color.js';

const KEYBINDING_NAME = 'show-capture-overlay';
const LOG_PREFIX = '[snipsnap-shell-bridge]';
const UNLOCKED_ACTION_MODES = unlockedActionModes(Shell.ActionMode);
const EDITOR_WINDOW_TIMEOUT_MS = 1_500;
const EDITOR_ACTIVATION_MAX_FRAMES = 8;
const selectionEncodeGate = new UncancellableWorkGate();

function monotonicMicroseconds() {
    return GLib.get_monotonic_time();
}

function logEvent(event, fields = {}) {
    console.log(`${LOG_PREFIX} ${JSON.stringify({
        component: 'snipsnap-shell-bridge',
        event,
        monotonic_us: monotonicMicroseconds(),
        ...fields,
    })}`);
}

// The keybinding handler is the top of an async call chain with nobody above
// it to catch a rejection, so without this a throw anywhere in the capture
// path vanishes and the symptom is an unresponsive shortcut with an empty
// journal -- indistinguishable from the key never arriving at all.
function logCaptureFailure(error) {
    logEvent('trigger-failed', {
        error: String(error?.message ?? error),
    });
    console.error(`${LOG_PREFIX} capture failed`, error);
}

export default class SnipSnapShellBridgeExtension extends Extension {
    enable() {
        this._enabled = true;
        this._captureLifecycle ??= new CaptureLifecycle();
        this._captureLifecycle.enable();
        this._handoffState ??= new HandoffState();
        this._handoffState.cancel();
        this._captureStartedUs = 0;
        this._root = null;
        this._snapshotActor = null;
        this._selectionActor = null;
        this._dimensionLabel = null;
        this._instructionsActor = null;
        this._shadeActors = null;
        this._selection = null;
        this._dragAnchor = null;
        this._dragging = false;
        this._modalGrab = null;
        this._afterPaintId = 0;
        this._captureContent = null;
        this._captureScale = 1;
        this._captureTopology = null;
        this._topologyGeneration ??= 0;
        this._topologyGeneration = this._topologyGeneration >=
            Number.MAX_SAFE_INTEGER ? 1 : this._topologyGeneration + 1;
        this._activeRequest = null;
        this._handoffCancellable = null;
        this._editorWindowCreatedId = 0;
        this._editorWindow = null;
        this._editorWindowOperation = null;
        this._editorWindowCandidates = new Map();
        this._editorCandidateIdleId = 0;
        this._editorPeerPid = 0;
        this._editorWindowWaiter = null;
        this._handoffCommitOperation = null;
        this._editorActivationLaterId = 0;
        this._keybindingChangedId = 0;

        this._settings = this.getSettings();
        this._interfaceSettings = this._openInterfaceSettings();
        this._accentChangedId = this._interfaceSettings?.connect(
            'changed::accent-color', () => this._applyAccentStyles()) ?? 0;
        const action = Main.wm.addKeybinding(
            KEYBINDING_NAME,
            this._settings,
            Meta.KeyBindingFlags.IGNORE_AUTOREPEAT,
            UNLOCKED_ACTION_MODES,
            () => this._triggerCapture().catch(logCaptureFailure)
        );

        if (action === Meta.KeyBindingAction.NONE) {
            logEvent('keybinding-unavailable', {
                binding: KEYBINDING_NAME,
            });
        }

        // Mutter is supposed to retrack settings-backed keybindings, but a
        // live reassignment to Print demonstrably did not take on GNOME 50
        // (reference desktop, 2026-07-23: activation moved the key after
        // enable and the handler never fired until a disable/enable cycle).
        // Re-grab explicitly whenever the key changes.
        this._keybindingChangedId = this._settings.connect(
            `changed::${KEYBINDING_NAME}`, () => {
                Main.wm.removeKeybinding(KEYBINDING_NAME);
                const regrabbed = Main.wm.addKeybinding(
                    KEYBINDING_NAME,
                    this._settings,
                    Meta.KeyBindingFlags.IGNORE_AUTOREPEAT,
                    UNLOCKED_ACTION_MODES,
                    () => this._triggerCapture().catch(logCaptureFailure)
                );
                logEvent('keybinding-regrabbed', {
                    action: Number(regrabbed),
                });
            });

        this._sessionModeId = Main.sessionMode.connect('updated', () => {
            if (!this._isUserSession()) {
                this._cancelEditorActivation();
                this._cleanupOverlay('session-mode-changed');
            }
        });
        this._monitorsChangedId = Main.layoutManager.connect(
            'monitors-changed', () => {
                this._topologyGeneration = this._topologyGeneration >=
                    Number.MAX_SAFE_INTEGER ? 1 : this._topologyGeneration + 1;
                this._cleanupOverlay('monitors-changed');
            });

        logEvent('enabled', {
            action: Number(action),
            session_mode: Main.sessionMode.currentMode,
        });
    }

    disable() {
        this._enabled = false;
        this._captureLifecycle.disable();
        this._cancelEditorActivation();

        Main.wm.removeKeybinding(KEYBINDING_NAME);

        if (this._keybindingChangedId) {
            this._settings?.disconnect(this._keybindingChangedId);
            this._keybindingChangedId = 0;
        }
        if (this._accentChangedId) {
            this._interfaceSettings?.disconnect(this._accentChangedId);
            this._accentChangedId = 0;
        }
        this._interfaceSettings = null;
        if (this._sessionModeId) {
            Main.sessionMode.disconnect(this._sessionModeId);
            this._sessionModeId = 0;
        }
        if (this._monitorsChangedId) {
            Main.layoutManager.disconnect(this._monitorsChangedId);
            this._monitorsChangedId = 0;
        }

        this._cleanupOverlay('disabled');
        this._settings = null;
        logEvent('disabled');
    }

    _isUserSession() {
        // Accept the primary interactive session and modes derived from it:
        // stock GNOME reports currentMode 'user'; Ubuntu reports 'ubuntu' with
        // parentMode 'user'. Hard-coding === 'user' refused every capture on
        // Ubuntu. isLocked/isGreeter/hasWindows still gate an unsafe capture.
        return this._enabled &&
            (Main.sessionMode.currentMode === 'user' ||
                Main.sessionMode.parentMode === 'user') &&
            !Main.sessionMode.isLocked &&
            !Main.sessionMode.isGreeter &&
            Main.sessionMode.hasWindows;
    }

    _snapshotTopology() {
        const stageViewScales = global.stage.peek_stage_views()
            .map(view => Number(view.get_scale()));
        return snapshotCaptureTopology({
            width: Math.floor(global.stage.width),
            height: Math.floor(global.stage.height),
        }, Main.layoutManager.monitors, this._topologyGeneration,
        stageViewScales);
    }

    async _triggerCapture() {
        this._cancelEditorActivation();
        const captureId = this._captureLifecycle.nextCaptureId();
        const triggerUs = monotonicMicroseconds();

        logEvent('trigger', {
            capture_id: captureId,
            session_mode: Main.sessionMode.currentMode,
        });

        if (!this._isUserSession()) {
            logEvent('trigger-refused', {
                capture_id: captureId,
                reason: 'not-unlocked-user-session',
            });
            return;
        }

        if (this._captureLifecycle.busy || this._root) {
            logEvent('trigger-refused', {
                capture_id: captureId,
                reason: 'capture-busy',
            });
            return;
        }
        if (selectionEncodeGate.busy) {
            logEvent('trigger-refused', {
                capture_id: captureId,
                reason: 'selection-encode-in-flight',
            });
            return;
        }

        const request = this._captureLifecycle.beginRequest(captureId);
        if (request === null) {
            logEvent('trigger-refused', {
                capture_id: captureId,
                reason: 'capture-lifecycle-inactive',
            });
            return;
        }

        this._captureStartedUs = triggerUs;
        const shooter = new Shell.Screenshot();
        let overlayRetained = false;

        try {
            const requestedTopology = this._snapshotTopology();
            validateStageCaptureBudget(requestedTopology);
            // GNOME Shell 50 and 51 promisify this method while loading their
            // own screenshot service. This stays inside the compositor:
            // no portal, flash, sound, PNG encode, or disk round trip.
            const [content, scale] =
                await shooter.screenshot_stage_to_content();

            if (!this._captureLifecycle.isCurrent(request) ||
                !this._isUserSession()) {
                logEvent('capture-discarded', {
                    capture_id: captureId,
                    reason: 'session-or-request-changed',
                });
                return;
            }

            const capturedTopology = this._snapshotTopology();
            if (!captureTopologiesEqual(
                requestedTopology, capturedTopology)) {
                logEvent('capture-discarded', {
                    capture_id: captureId,
                    reason: 'topology-changed-during-capture',
                });
                return;
            }
            const texture = content?.get_texture?.();
            if (!texture)
                throw new Error('captured stage texture is unavailable');
            const capturedStage = validateCapturedStage(
                capturedTopology,
                scale,
                {
                    width: texture.get_width(),
                    height: texture.get_height(),
                }
            );

            logEvent('capture-ready', {
                capture_id: captureId,
                elapsed_us: monotonicMicroseconds() - triggerUs,
                scale,
                texture_width: capturedStage.textureWidth,
                texture_height: capturedStage.textureHeight,
                ...topologyLogFields(capturedTopology),
            });

            this._showOverlay(content, scale, request, capturedTopology);
            overlayRetained = true;
        } catch (error) {
            if (!this._captureLifecycle.isCurrent(request)) {
                logEvent('capture-discarded', {
                    capture_id: captureId,
                    reason: 'request-invalidated-after-error',
                });
                return;
            }

            logError(error, `${LOG_PREFIX} stage capture failed`);
            logEvent('capture-failed', {
                capture_id: captureId,
                message: String(error?.message ?? error),
            });
            this._cleanupOverlay('capture-failed');
        } finally {
            if (!overlayRetained)
                this._captureLifecycle.finishRequest(request);
        }
    }

    _showOverlay(content, scale, request, captureTopology) {
        const {captureId} = request;
        const root = new St.Widget({
            name: 'snipsnap-shell-bridge-overlay',
            style_class: 'snipsnap-shell-bridge-overlay',
            reactive: true,
            can_focus: true,
            visible: false,
            // Every child below is placed at explicit stage coordinates.
            // BinLayout aligns children within the parent instead of honouring
            // those coordinates, which silently re-centred the top shade the
            // moment a drag shrank it below the full stage height: the top of
            // the screen lost its dim and the band where the re-centred rect
            // overlapped the others went double-dark.
            layout_manager: new Clutter.FixedLayout(),
        });
        root.add_constraint(new Clutter.BindConstraint({
            source: global.stage,
            coordinate: Clutter.BindCoordinate.ALL,
        }));
        root.set_cursor_type(Clutter.CursorType.CROSSHAIR);

        const snapshotActor = new St.Widget({
            style_class: 'snipsnap-shell-bridge-snapshot',
            reactive: false,
        });
        snapshotActor.add_constraint(new Clutter.BindConstraint({
            source: root,
            coordinate: Clutter.BindCoordinate.ALL,
        }));
        snapshotActor.set_content(content);
        root.add_child(snapshotActor);

        const shadeActors = {};
        for (const name of ['top', 'bottom', 'left', 'right']) {
            shadeActors[name] = new St.Widget({
                style_class: 'snipsnap-shell-bridge-shade',
                reactive: false,
            });
            root.add_child(shadeActors[name]);
        }

        const selectionActor = new St.Widget({
            style_class: 'snipsnap-shell-bridge-selection',
            reactive: false,
            visible: false,
        });
        root.add_child(selectionActor);

        const dimensionLabel = new St.Label({
            style_class: 'snipsnap-shell-bridge-dimensions',
            reactive: false,
            visible: false,
        });
        root.add_child(dimensionLabel);

        const instructions = new St.Label({
            style_class: 'snipsnap-shell-bridge-instructions',
            text: 'Drag to select  •  Enter to accept  •  Esc to cancel',
            reactive: false,
        });
        root.add_child(instructions);

        this._root = root;
        this._snapshotActor = snapshotActor;
        this._selectionActor = selectionActor;
        this._dimensionLabel = dimensionLabel;
        this._instructionsActor = instructions;
        this._shadeActors = shadeActors;
        this._selection = null;
        this._dragAnchor = null;
        this._dragging = false;
        this._captureScale = scale;
        this._captureContent = content;
        this._captureTopology = captureTopology;
        this._activeRequest = request;
        this._activeCaptureId = captureId;

        this._applyAccentStyles();

        root.connect('captured-event', (_actor, event) =>
            this._onCapturedEvent(event));

        Main.layoutManager.emit('system-modal-opened');
        const {screenshotUIGroup} = Main.layoutManager;
        screenshotUIGroup.add_child(root);
        screenshotUIGroup.get_parent().set_child_above_sibling(
            screenshotUIGroup, null);

        this._positionInstructions(instructions);
        this._setFullStageShade();
        this._armFirstPaint(captureId);

        this._modalGrab = Main.pushModal(root, {
            actionMode: Shell.ActionMode.POPUP,
        });
        root.show();
        global.stage.queue_redraw();
    }

    _positionInstructions(instructions) {
        const [, naturalWidth] = instructions.get_preferred_width(-1);
        instructions.set_position(
            Math.max(12, Math.floor((global.stage.width - naturalWidth) / 2)),
            24
        );
    }

    _armFirstPaint(captureId) {
        if (this._afterPaintId)
            global.stage.disconnect(this._afterPaintId);

        this._afterPaintId = global.stage.connect('after-paint', () => {
            const signalId = this._afterPaintId;
            this._afterPaintId = 0;
            if (signalId)
                global.stage.disconnect(signalId);

            if (!this._root || captureId !== this._activeCaptureId)
                return;

            logEvent('first-paint', {
                capture_id: captureId,
                elapsed_us: monotonicMicroseconds() - this._captureStartedUs,
                stage_width: Math.floor(global.stage.width),
                stage_height: Math.floor(global.stage.height),
                topology_id: this._captureTopology?.id ?? null,
            });
        });
    }

    _onCapturedEvent(event) {
        switch (event.type()) {
        case Clutter.EventType.BUTTON_PRESS:
            return this._onButtonPress(event);
        case Clutter.EventType.MOTION:
            return this._onPointerMotion(event);
        case Clutter.EventType.BUTTON_RELEASE:
            return this._onButtonRelease(event);
        case Clutter.EventType.KEY_PRESS:
            return this._onKeyPress(event);
        default:
            return Clutter.EVENT_PROPAGATE;
        }
    }

    _onButtonPress(event) {
        if (event.get_button() !== Clutter.BUTTON_PRIMARY)
            return Clutter.EVENT_PROPAGATE;
        if (this._handoffState.busy)
            return Clutter.EVENT_STOP;

        const [x, y] = event.get_coords();
        this._dragAnchor = {x, y};
        this._dragging = true;
        this._updateSelection({x, y});

        logEvent('selection-started', {
            capture_id: this._activeCaptureId,
            x: Math.floor(x),
            y: Math.floor(y),
        });
        return Clutter.EVENT_STOP;
    }

    _onPointerMotion(event) {
        if (this._handoffState.busy)
            return Clutter.EVENT_STOP;
        if (!this._dragging)
            return Clutter.EVENT_PROPAGATE;

        const [x, y] = event.get_coords();
        this._updateSelection({x, y});
        return Clutter.EVENT_STOP;
    }

    _onButtonRelease(event) {
        if (this._handoffState.busy)
            return Clutter.EVENT_STOP;
        if (!this._dragging ||
            event.get_button() !== Clutter.BUTTON_PRIMARY) {
            return Clutter.EVENT_PROPAGATE;
        }

        const [x, y] = event.get_coords();
        this._updateSelection({x, y});
        this._dragging = false;

        logEvent('selection', {
            capture_id: this._activeCaptureId,
            ...this._selection,
            scale: this._captureScale,
            topology_id: this._captureTopology?.id ?? null,
            elapsed_us: monotonicMicroseconds() - this._captureStartedUs,
        });
        return Clutter.EVENT_STOP;
    }

    _onKeyPress(event) {
        const symbol = event.get_key_symbol();
        if (symbol === Clutter.KEY_Escape) {
            if (this._handoffCommitOperation !== null) {
                logEvent('cancel-refused', {
                    capture_id: this._activeCaptureId,
                    reason: 'commit-in-progress',
                });
                return Clutter.EVENT_STOP;
            }
            logEvent('cancelled', {
                capture_id: this._activeCaptureId,
                reason: 'escape',
            });
            this._cleanupOverlay('escape');
            return Clutter.EVENT_STOP;
        }

        if ([Clutter.KEY_Return, Clutter.KEY_KP_Enter,
            Clutter.KEY_ISO_Enter].includes(symbol)) {
            if (!this._selection)
                return Clutter.EVENT_STOP;

            this._beginHandoff();
            return Clutter.EVENT_STOP;
        }

        return Clutter.EVENT_STOP;
    }

    _openInterfaceSettings() {
        // Named accents are GNOME 47+. On anything older the key is absent and
        // the stylesheet's own colours stay in force, so this returns null
        // rather than guessing.
        const source = Gio.SettingsSchemaSource.get_default();
        const schema = source?.lookup('org.gnome.desktop.interface', true);
        if (!schema?.has_key('accent-color'))
            return null;
        return new Gio.Settings({settings_schema: schema});
    }

    /**
     * Repaint the accent-carrying actors from the desktop accent. Safe to call
     * with no overlay open: the next capture picks the colour up when it builds
     * its actors, so a live accent change applies immediately either way.
     */
    _applyAccentStyles() {
        if (!this._interfaceSettings)
            return;
        const styles = overlayAccentStyles(
            this._interfaceSettings.get_string('accent-color'));
        this._selectionActor?.set_style(styles.selection);
        this._dimensionLabel?.set_style(styles.label);
        this._instructionsActor?.set_style(styles.label);
    }

    _stageBounds() {
        return {
            width: Math.max(1, Math.floor(global.stage.width)),
            height: Math.max(1, Math.floor(global.stage.height)),
        };
    }

    _setFullStageShade() {
        const bounds = this._stageBounds();
        this._shadeActors.top.set_position(0, 0);
        this._shadeActors.top.set_size(bounds.width, bounds.height);
        for (const name of ['bottom', 'left', 'right'])
            this._shadeActors[name].set_size(0, 0);
    }

    _updateSelection(pointer) {
        if (!this._dragAnchor || !this._root)
            return;

        const bounds = this._stageBounds();
        const selection = selectionFromPoints(
            this._dragAnchor, pointer, bounds);
        this._selection = selection;

        const shades = shadeRectangles(selection, bounds);
        for (const name of ['top', 'bottom', 'left', 'right']) {
            const rect = shades[name];
            this._shadeActors[name].set_position(rect.x, rect.y);
            this._shadeActors[name].set_size(rect.width, rect.height);
        }

        this._selectionActor.set_position(selection.x, selection.y);
        this._selectionActor.set_size(selection.width, selection.height);
        this._selectionActor.show();

        this._dimensionLabel.set_text(formatDimensions(selection));
        const [, labelWidth] = this._dimensionLabel.get_preferred_width(-1);
        const [, labelHeight] = this._dimensionLabel.get_preferred_height(
            labelWidth);
        const position = labelPosition(selection, {
            width: labelWidth,
            height: labelHeight,
        }, bounds);
        this._dimensionLabel.set_position(position.x, position.y);
        this._dimensionLabel.show();
    }

    _isCurrentHandoff(request, operation) {
        return this._root !== null &&
            this._activeRequest === request &&
            this._captureLifecycle.isCurrent(request) &&
            this._handoffState.isCurrent(operation, request);
    }

    _beginHandoff() {
        const request = this._activeRequest;
        if (!request ||
            !this._captureLifecycle.isCurrent(request) ||
            !this._captureContent ||
            !this._captureTopology ||
            !this._selection) {
            return;
        }

        let currentTopology;
        try {
            currentTopology = this._snapshotTopology();
        } catch (error) {
            logError(error, `${LOG_PREFIX} capture topology became invalid`);
        }
        if (!captureTopologiesEqual(
            this._captureTopology, currentTopology)) {
            logEvent('handoff-refused', {
                capture_id: request.captureId,
                reason: 'topology-changed-before-handoff',
            });
            this._cleanupOverlay('topology-changed-before-handoff', request);
            return;
        }

        const encodeOperation = selectionEncodeGate.begin();
        if (!encodeOperation) {
            logEvent('handoff-refused', {
                capture_id: request.captureId,
                reason: 'selection-encode-in-flight',
            });
            return;
        }
        const operation = this._handoffState.begin(request);
        if (!operation) {
            selectionEncodeGate.finish(encodeOperation);
            logEvent('handoff-refused', {
                capture_id: request.captureId,
                reason: 'handoff-busy',
            });
            return;
        }

        const selection = Object.freeze({...this._selection});
        const content = this._captureContent;
        const scale = this._captureScale;
        const captureTopology = this._captureTopology;
        const cancellable = new Gio.Cancellable();
        this._handoffCancellable = cancellable;
        this._instructionsActor?.set_text(
            'Opening SnipSnap…  •  Esc to cancel');

        logEvent('handoff-started', {
            capture_id: request.captureId,
            ...selection,
            scale,
            topology_id: captureTopology.id,
            elapsed_us: monotonicMicroseconds() - this._captureStartedUs,
        });
        void this._handoffSelection(
            request, operation, content, selection, scale, captureTopology,
            encodeOperation, cancellable);
    }

    async _handoffSelection(
        request, operation, content, selection, scale, captureTopology,
        encodeOperation, cancellable) {
        try {
            let encoded;
            try {
                encoded = await encodeSelectedRegion(
                    content, selection, scale, captureTopology);
            } finally {
                selectionEncodeGate.finish(encodeOperation);
            }
            if (!this._isCurrentHandoff(request, operation))
                return;

            const captureId = BigInt(request.captureId);
            const frame = encodeRequest({
                captureId,
                logicalX: encoded.pixelRegion.logicalX,
                logicalY: encoded.pixelRegion.logicalY,
                logicalWidth: encoded.pixelRegion.logicalWidth,
                logicalHeight: encoded.pixelRegion.logicalHeight,
                pixelWidth: encoded.pixelRegion.width,
                pixelHeight: encoded.pixelRegion.height,
                scaleNumerator: encoded.scaleFraction.numerator,
                scaleDenominator: encoded.scaleFraction.denominator,
                png: encoded.png,
            });
            if (!this._isCurrentHandoff(request, operation))
                return;

            logEvent('handoff-encoded', {
                capture_id: request.captureId,
                pixel_width: encoded.pixelRegion.width,
                pixel_height: encoded.pixelRegion.height,
                png_bytes: encoded.png.byteLength,
                topology_id: captureTopology.id,
                elapsed_us: monotonicMicroseconds() - this._captureStartedUs,
            });

            // Frozen per capture so a mid-handoff settings change cannot
            // desynchronize the two sides of the commit handshake.
            const externalAnnotator =
                this._settings?.get_boolean('external-annotator') === true;
            const {daemonPid} = await handoffFrame(
                frame,
                captureId,
                cancellable,
                peerPid => {
                    if (!this._isCurrentHandoff(request, operation))
                        throw new Error('handoff request changed before peer auth');
                    if (!externalAnnotator) {
                        this._armEditorWindowObserver(
                            request, operation, peerPid);
                    }
                },
                async peerPid => {
                    if (externalAnnotator) {
                        // The daemon hands committed captures to an external
                        // annotator; no daemon-owned editor window will ever
                        // exist, so commit without observing one.
                        return;
                    }
                    const window = await this._waitForEditorWindow(
                        request, operation, cancellable);
                    if (!this._isCurrentHandoff(request, operation) ||
                        !this._matchesEditorWindow(
                            window, peerPid, request.captureId)) {
                        throw new Error(
                            'editor window identity changed before commit');
                    }
                },
                () => {
                    if (!this._isCurrentHandoff(request, operation))
                        throw new Error('handoff request changed before commit');
                    this._handoffCommitOperation = operation;
                    this._instructionsActor?.set_text(
                        'Finalizing SnipSnap…');
                }
            );
            if (!this._isCurrentHandoff(request, operation))
                return;
            if (externalAnnotator) {
                // The annotator owns its own window; the daemon spawned it on
                // commit and there is nothing to focus or verify here.
                logEvent('commit-accepted', {
                    capture_id: request.captureId,
                    daemon_pid: daemonPid,
                    annotator: 'external',
                    elapsed_us:
                        monotonicMicroseconds() - this._captureStartedUs,
                });
                this._cleanupOverlay('commit-accepted', request);
                return;
            }
            const editorWindow = this._editorWindow;
            const editorIdentityValid = editorWindow !== null &&
                this._matchesEditorWindow(
                    editorWindow, this._editorPeerPid, request.captureId);
            if (!editorIdentityValid) {
                logEvent('commit-window-lost', {
                    capture_id: request.captureId,
                    daemon_pid: daemonPid,
                });
                this._cleanupOverlay(
                    'commit-accepted-window-lost', request);
                return;
            }

            logEvent('commit-accepted', {
                capture_id: request.captureId,
                daemon_pid: daemonPid,
                elapsed_us: monotonicMicroseconds() - this._captureStartedUs,
            });
            this._cleanupOverlay('commit-accepted', request);
            this._scheduleEditorActivation(
                editorWindow, daemonPid, request.captureId);
        } catch (error) {
            if (!this._isCurrentHandoff(request, operation))
                return;

            if (error?.commitSent === true) {
                const editorWindow = this._editorWindow;
                const editorPeerPid = this._editorPeerPid;
                const editorIdentityValid = editorWindow !== null &&
                    this._matchesEditorWindow(
                        editorWindow, editorPeerPid, request.captureId);
                logError(
                    error,
                    `${LOG_PREFIX} commit ACK outcome is indeterminate`);
                logEvent('commit-ack-indeterminate', {
                    capture_id: request.captureId,
                    editor_identity_valid: editorIdentityValid,
                    message: String(error?.message ?? error),
                });
                this._cleanupOverlay(
                    'commit-ack-indeterminate', request);
                if (editorIdentityValid) {
                    this._scheduleEditorActivation(
                        editorWindow, editorPeerPid, request.captureId);
                }
                return;
            }

            logError(error, `${LOG_PREFIX} selected-region handoff failed`);
            logEvent('handoff-failed', {
                capture_id: request.captureId,
                message: String(error?.message ?? error),
                status: error?.status ?? null,
            });
            this._handoffState.finish(operation);
            if (this._handoffCommitOperation === operation)
                this._handoffCommitOperation = null;
            this._disarmEditorWindowObserver(operation);
            if (this._handoffCancellable === cancellable)
                this._handoffCancellable = null;
            // BUSY is not an outage: the daemon refused because a committed
            // editor is still open. Tell the user the actual remedy.
            this._instructionsActor?.set_text(error?.status === AckStatus.BUSY
                ? 'Close the open SnipSnap editor first  •  ' +
                  'Enter to retry  •  Esc to cancel'
                : 'SnipSnap unavailable  •  Enter to retry  •  Esc to cancel');
        } finally {
            if (this._handoffCommitOperation === operation)
                this._handoffCommitOperation = null;
            this._disarmEditorWindowObserver(operation);
            if (this._handoffState.finish(operation) &&
                this._handoffCancellable === cancellable) {
                this._handoffCancellable = null;
            }
        }
    }

    _windowDescriptor(window) {
        const identities = [];
        for (const getter of [
            'get_wm_class',
            'get_wm_class_instance',
            'get_gtk_application_id',
        ]) {
            try {
                const identity = window[getter]?.call(window);
                if (identity)
                    identities.push(identity);
            } catch (_error) {
                // A missing optional identity cannot broaden the match.
            }
        }

        let pid = null;
        let windowType = null;
        let title = null;
        try {
            pid = window.get_pid();
            windowType = window.get_window_type();
            title = window.get_title();
        } catch (_error) {
            // An unmanaged or incomplete window must fail closed.
        }

        return {pid, windowType, title, identities};
    }

    _matchesEditorWindow(window, peerPid, captureId) {
        return matchesEditorWindow(
            this._windowDescriptor(window),
            peerPid,
            Meta.WindowType.NORMAL,
            captureId);
    }

    _armEditorWindowObserver(request, operation, peerPid) {
        this._disarmEditorWindowObserver();
        this._editorWindowOperation = operation;
        this._editorPeerPid = peerPid;
        this._editorWindowCreatedId = global.display.connect(
            'window-created', (_display, window) => {
                if (!this._isCurrentHandoff(request, operation) ||
                    this._editorWindow) {
                    return;
                }
                const descriptor = this._windowDescriptor(window);
                if (descriptor.pid !== peerPid ||
                    descriptor.windowType !== Meta.WindowType.NORMAL ||
                    this._editorWindowCandidates.size >= 8) {
                    return;
                }

                const notifyId = window.connect('notify', () =>
                    this._evaluateEditorWindowCandidate(
                        request, operation, window));
                const unmanagedId = window.connect('unmanaged', () => {
                    const signals = this._editorWindowCandidates.get(window);
                    if (signals?.notifyId) {
                        try {
                            window.disconnect(signals.notifyId);
                        } catch (_error) {
                            // The candidate is already leaving Mutter.
                        }
                    }
                    this._editorWindowCandidates.delete(window);
                    if (this._editorWindow === window)
                        this._editorWindow = null;
                });
                this._editorWindowCandidates.set(window, {
                    notifyId,
                    unmanagedId,
                });
                this._evaluateEditorWindowCandidate(
                    request, operation, window);
                this._scheduleEditorCandidateCheck(request, operation);
            });
    }

    _evaluateEditorWindowCandidate(request, operation, window) {
        if (!this._isCurrentHandoff(request, operation) ||
            this._editorWindow ||
            !this._editorWindowCandidates.has(window) ||
            !this._matchesEditorWindow(
                window, this._editorPeerPid, request.captureId)) {
            return false;
        }

        this._editorWindow = window;
        this._resolveEditorWindowWaiter(window);
        logEvent('editor-window-observed', {
            capture_id: request.captureId,
            daemon_pid: this._editorPeerPid,
            title: editorWindowTitle(request.captureId),
        });
        return true;
    }

    _waitForEditorWindow(request, operation, cancellable) {
        if (this._editorWindow)
            return Promise.resolve(this._editorWindow);
        if (this._editorWindowWaiter)
            return Promise.reject(new Error('editor window wait is already active'));

        return new Promise((resolve, reject) => {
            const waiter = {
                request,
                operation,
                cancellable,
                cancelledId: 0,
                timeoutId: 0,
                resolve,
                reject,
            };
            waiter.cancelledId = cancellable.connect(() => {
                if (this._editorWindowWaiter !== waiter)
                    return;
                this._rejectEditorWindowWaiter(
                    new Error('editor window wait was cancelled'));
            });
            waiter.timeoutId = GLib.timeout_add(
                GLib.PRIORITY_DEFAULT,
                EDITOR_WINDOW_TIMEOUT_MS,
                () => {
                    waiter.timeoutId = 0;
                    if (this._editorWindowWaiter === waiter) {
                        this._rejectEditorWindowWaiter(
                            new Error('authenticated editor window timed out'));
                    }
                    return GLib.SOURCE_REMOVE;
                }
            );
            this._editorWindowWaiter = waiter;
            if (cancellable.is_cancelled()) {
                this._rejectEditorWindowWaiter(
                    new Error('editor window wait was cancelled'));
            }
        });
    }

    _resolveEditorWindowWaiter(window) {
        const waiter = this._editorWindowWaiter;
        if (!waiter ||
            !this._isCurrentHandoff(waiter.request, waiter.operation)) {
            return false;
        }

        this._editorWindowWaiter = null;
        if (waiter.cancelledId) {
            try {
                waiter.cancellable.disconnect(waiter.cancelledId);
            } catch (_error) {
                // Cancellation may be dispatching concurrently.
            }
        }
        if (waiter.timeoutId)
            GLib.source_remove(waiter.timeoutId);
        waiter.resolve(window);
        return true;
    }

    _rejectEditorWindowWaiter(error) {
        const waiter = this._editorWindowWaiter;
        if (!waiter)
            return false;

        this._editorWindowWaiter = null;
        if (waiter.cancelledId) {
            try {
                waiter.cancellable.disconnect(waiter.cancelledId);
            } catch (_disconnectError) {
                // Cancellation may be dispatching concurrently.
            }
        }
        if (waiter.timeoutId)
            GLib.source_remove(waiter.timeoutId);
        waiter.reject(error);
        return true;
    }

    _scheduleEditorCandidateCheck(request, operation) {
        if (this._editorCandidateIdleId)
            return;

        this._editorCandidateIdleId = GLib.idle_add(
            GLib.PRIORITY_DEFAULT_IDLE,
            () => {
                this._editorCandidateIdleId = 0;
                for (const window of this._editorWindowCandidates.keys()) {
                    if (this._evaluateEditorWindowCandidate(
                        request, operation, window)) {
                        break;
                    }
                }
                return GLib.SOURCE_REMOVE;
            }
        );
    }

    _disarmEditorWindowObserver(expectedOperation = null) {
        if (expectedOperation !== null &&
            this._editorWindowOperation !== expectedOperation) {
            return false;
        }

        if (this._editorWindowCreatedId) {
            global.display.disconnect(this._editorWindowCreatedId);
            this._editorWindowCreatedId = 0;
        }
        this._rejectEditorWindowWaiter(
            new Error('editor window observation ended'));
        if (this._editorCandidateIdleId) {
            GLib.source_remove(this._editorCandidateIdleId);
            this._editorCandidateIdleId = 0;
        }
        for (const [window, signals] of this._editorWindowCandidates) {
            try {
                if (signals.notifyId)
                    window.disconnect(signals.notifyId);
                if (signals.unmanagedId)
                    window.disconnect(signals.unmanagedId);
            } catch (_error) {
                // The window may already have been unmanaged.
            }
        }
        this._editorWindowCandidates.clear();
        this._editorWindow = null;
        this._editorWindowOperation = null;
        this._editorPeerPid = 0;
        return true;
    }

    _scheduleEditorActivation(editorWindow, peerPid, captureId) {
        this._cancelEditorActivation();
        const laters = global.compositor.get_laters();
        let remainingFrames = EDITOR_ACTIVATION_MAX_FRAMES;
        this._editorActivationLaterId = laters.add(
            Meta.LaterType.BEFORE_REDRAW,
            () => {
                if (!this._isUserSession() ||
                    !this._matchesEditorWindow(
                        editorWindow, peerPid, captureId)) {
                    this._editorActivationLaterId = 0;
                    return GLib.SOURCE_REMOVE;
                }

                let actor = null;
                try {
                    actor = editorWindow.get_compositor_private();
                } catch (_error) {
                    // Mutter may not have attached the compositor actor on
                    // the first frame after the modal overlay is released.
                }

                if (!actor?.mapped && --remainingFrames > 0)
                    return GLib.SOURCE_CONTINUE;

                this._editorActivationLaterId = 0;
                if (!actor?.mapped)
                    return GLib.SOURCE_REMOVE;

                Main.activateWindow(editorWindow);
                logEvent('editor-focus-requested', {
                    capture_id: captureId,
                    daemon_pid: peerPid,
                });
                return GLib.SOURCE_REMOVE;
            }
        );
    }

    _cancelEditorActivation() {
        if (!this._editorActivationLaterId)
            return;
        const laters = global.compositor.get_laters();
        laters.remove(this._editorActivationLaterId);
        this._editorActivationLaterId = 0;
    }

    _cleanupOverlay(reason, expectedRequest = null) {
        if (expectedRequest !== null &&
            (this._activeRequest !== expectedRequest ||
             !this._captureLifecycle.isCurrent(expectedRequest))) {
            return false;
        }

        const request = this._activeRequest;
        this._handoffState.cancel();
        this._handoffCommitOperation = null;
        this._disarmEditorWindowObserver();
        if (this._handoffCancellable)
            this._handoffCancellable.cancel();
        this._handoffCancellable = null;

        if (this._afterPaintId) {
            global.stage.disconnect(this._afterPaintId);
            this._afterPaintId = 0;
        }

        if (this._modalGrab) {
            try {
                Main.popModal(this._modalGrab);
            } catch (error) {
                logError(error, `${LOG_PREFIX} failed to release modal grab`);
            }
            this._modalGrab = null;
        }

        if (this._snapshotActor)
            this._snapshotActor.set_content(null);

        const hadOverlay = this._root !== null;
        if (this._root)
            this._root.destroy();

        this._root = null;
        this._snapshotActor = null;
        this._selectionActor = null;
        this._dimensionLabel = null;
        this._instructionsActor = null;
        this._shadeActors = null;
        this._selection = null;
        this._dragAnchor = null;
        this._dragging = false;
        this._captureContent = null;
        this._captureScale = 1;
        this._captureTopology = null;
        this._activeRequest = null;
        this._activeCaptureId = 0;

        if (!this._captureLifecycle.finishRequest(request))
            this._captureLifecycle.invalidateRequest();

        if (hadOverlay) {
            logEvent('overlay-closed', {
                capture_id: request?.captureId ?? 0,
                reason,
            });
        }
        return hadOverlay;
    }
}
