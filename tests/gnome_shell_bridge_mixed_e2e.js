// SPDX-License-Identifier: GPL-3.0-or-later

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const STARTUP_PHASE_MARKER = 'SNIPSNAP-E2E-STARTUP-PHASE';
const IMPLEMENTATION_URI = './gnome_shell_bridge_mixed_e2e_impl.js';

// GNOME Shell starts its layout animation before it awaits this module, then
// connects the automation startup-complete handler only after init() returns.
// Keep the entry point tiny so the compositor signal cannot race a large
// static module graph; load the real test only after Shell invokes run().

function logStartupPhase(phase) {
    const startingUp = Main.layoutManager._startingUp;
    console.log(`${STARTUP_PHASE_MARKER} ${JSON.stringify({
        phase,
        layout_starting_up:
            typeof startingUp === 'boolean' ? startingUp : null,
        stage_mapped: Boolean(global.stage.mapped),
        stage_visible: Boolean(global.stage.visible),
        stage_width: Number(global.stage.width),
        stage_height: Number(global.stage.height),
    })}`);
}

function logSignalOnce(object, signal, phase) {
    const signalId = object.connect(signal, () => {
        object.disconnect(signalId);
        logStartupPhase(phase);
    });
}

export function init() {
    logStartupPhase('automation-module-ready');
    logSignalOnce(Main.layoutManager, 'startup-prepared', 'startup-prepared');
    logSignalOnce(global.stage, 'after-paint', 'stage-after-paint');
    logSignalOnce(Main.layoutManager, 'startup-complete', 'startup-complete');
}

export async function run() {
    const implementation = await import(IMPLEMENTATION_URI);
    return implementation.run();
}
