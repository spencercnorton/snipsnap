// SPDX-License-Identifier: GPL-3.0-or-later

import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

const ENTRY_URL = new URL('./gnome_shell_bridge_mixed_e2e.js', import.meta.url);
const MAIN_IMPORT =
    "import * as Main from 'resource:///org/gnome/shell/ui/main.js';";
const IMPLEMENTATION_IMPORT = 'await import(IMPLEMENTATION_URI)';

function signalTarget(properties = {}) {
    let nextId = 1;
    const handlers = new Map();
    return {
        ...properties,
        connect(signal, callback) {
            const id = nextId++;
            handlers.set(id, {signal, callback});
            return id;
        },
        disconnect(id) {
            assert.equal(handlers.delete(id), true);
        },
        emit(signal) {
            for (const {signal: connectedSignal, callback} of [...handlers.values()]) {
                if (connectedSignal === signal)
                    callback();
            }
        },
    };
}

async function loadInstrumentedEntry(source, main, loadImplementation) {
    assert.equal(source.split(MAIN_IMPORT).length, 2);
    assert.equal(source.split(IMPLEMENTATION_IMPORT).length, 2);
    const instrumented = source
        .replace(MAIN_IMPORT, 'const Main = globalThis.__snipsnapMain;')
        .replace(
            IMPLEMENTATION_IMPORT,
            'await globalThis.__snipsnapLoadImplementation()'
        );
    globalThis.__snipsnapMain = main;
    globalThis.__snipsnapLoadImplementation = loadImplementation;
    const encoded = Buffer.from(instrumented).toString('base64');
    return import(`data:text/javascript;base64,${encoded}#${randomUUID()}`);
}

test('mixed entry keeps the implementation out of Shell startup', async t => {
    const source = await readFile(ENTRY_URL, 'utf8');
    const staticImports = source.match(/^import .*;$/gm) ?? [];
    assert.deepEqual(staticImports, [MAIN_IMPORT]);
    assert.ok(Buffer.byteLength(source) < 2048, 'entry point must stay minimal');
    assert.match(source, /export function init\(\)/);
    assert.match(source, /export async function run\(\)/);

    const layoutManager = signalTarget({_startingUp: true});
    const stage = signalTarget({
        mapped: true,
        visible: true,
        width: 1280,
        height: 720,
    });
    let implementationLoads = 0;
    let implementationRuns = 0;
    const messages = [];
    const originalLog = console.log;
    console.log = message => messages.push(message);
    globalThis.stage = stage;
    t.after(() => {
        console.log = originalLog;
        delete globalThis.stage;
        delete globalThis.__snipsnapMain;
        delete globalThis.__snipsnapLoadImplementation;
    });

    const entry = await loadInstrumentedEntry(
        source,
        {layoutManager},
        async () => {
            implementationLoads++;
            return {
                async run() {
                    implementationRuns++;
                    return 'mixed-pass';
                },
            };
        }
    );
    assert.equal(implementationLoads, 0);

    entry.init();
    layoutManager.emit('startup-prepared');
    stage.emit('after-paint');
    stage.emit('after-paint');
    layoutManager._startingUp = false;
    layoutManager.emit('startup-complete');

    const phases = messages.map(message => {
        assert.match(message, /^SNIPSNAP-E2E-STARTUP-PHASE /);
        return JSON.parse(message.slice(message.indexOf('{')));
    });
    assert.deepEqual(phases.map(value => value.phase), [
        'automation-module-ready',
        'startup-prepared',
        'stage-after-paint',
        'startup-complete',
    ]);
    assert.equal(phases[0].layout_starting_up, true);
    assert.equal(phases.at(-1).layout_starting_up, false);
    assert.equal(implementationLoads, 0);

    assert.equal(await entry.run(), 'mixed-pass');
    assert.equal(implementationLoads, 1);
    assert.equal(implementationRuns, 1);
});
