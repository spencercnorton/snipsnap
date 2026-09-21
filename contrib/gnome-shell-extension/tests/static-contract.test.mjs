// SPDX-License-Identifier: GPL-3.0-or-later

import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectDirectory = path.dirname(testDirectory);
const uuid = 'snipsnap-shell-bridge@kleos.norvi.tech';
const sourceDirectory = 'snipsnap-shell-bridge';
const extensionDirectory = path.join(projectDirectory, sourceDirectory);

async function read(relativePath) {
    return readFile(path.join(projectDirectory, relativePath), 'utf8');
}

test('metadata limits the extension to audited Shell user sessions', async () => {
    const metadata = JSON.parse(await read(`${sourceDirectory}/metadata.json`));

    assert.equal(metadata.uuid, uuid);
    assert.deepEqual(metadata['shell-version'], ['50', '51']);
    assert.deepEqual(metadata['session-modes'], ['user']);
    assert.equal(metadata['settings-schema'],
        'org.gnome.shell.extensions.flameshot-shell-bridge');
});

test('capture path is compositor-local and contains no flash or sound calls', async () => {
    const source = await read(`${sourceDirectory}/extension.js`);
    const encoder = await read(`${sourceDirectory}/selected-region.js`);
    const client = await read(`${sourceDirectory}/handoff-client.js`);

    assert.match(source, /screenshot_stage_to_content\(\)/);
    assert.match(encoder, /Shell\.Screenshot\.composite_to_stream\(/);
    assert.match(client, /Gio\.UnixSocketAddress\.new\(/);
    assert.match(client, /set_enable_proxy\(false\)/);
    assert.match(client, /get_credentials\(\)/);
    assert.match(client, /get_unix_user\(\)/);
    assert.match(client, /get_unix_pid\(\)/);
    const dbusCalls = [...client.matchAll(
        /Gio\.DBus\.session\.call\(\s*'([^']*)',\s*'([^']*)',\s*'([^']*)',\s*'([^']*)',/g)];
    assert.equal(dbusCalls.length, 2);
    assert.deepEqual(dbusCalls.map(call => call[4]).sort(),
        ['GetConnectionUnixProcessID', 'StartServiceByName']);
    assert.match(client,
        /const SNIPSNAP_DBUS_SERVICE = 'tech\.norvi\.snipsnap';/);
    assert.match(client, /servicePid !== peerPid/);
    assert.match(client, /encodeCommit\(captureId\)/);
    assert.match(client, /AckStatus\.COMMIT_ACCEPTED/);
    assert.match(client, /commitStarted = true/);
    assert.match(source, /logEvent\('commit-ack-indeterminate'/);
    assert.match(source, /logEvent\('capture-ready'/);
    assert.match(source, /logEvent\('first-paint'/);
    assert.match(source, /logEvent\('selection'/);
    assert.match(source, /selectionEncodeGate\.busy/);
    assert.match(source, /reason: 'selection-encode-in-flight'/);
    assert.match(source, /selectionEncodeGate\.finish\(encodeOperation\)/);
    const allSources = `${source}\n${encoder}\n${client}`;
    assert.doesNotMatch(allSources,
        /play_from_theme|screen-capture|Flashspot|Main\.notify/);
    assert.doesNotMatch(allSources,
        /org\.freedesktop\.portal\.Screenshot|captureScreenshot\(/);
    assert.doesNotMatch(allSources,
        /show-screenshot-ui|set_strv|set_value|Gio\.File|Gio\.Subprocess/);
});

test('keybinding uses all explicitly unlocked Shell action modes', async () => {
    const source = await read(`${sourceDirectory}/extension.js`);

    assert.match(source,
        /const UNLOCKED_ACTION_MODES = unlockedActionModes\(Shell\.ActionMode\)/);
    assert.match(source,
        /Meta\.KeyBindingFlags\.IGNORE_AUTOREPEAT,\s*UNLOCKED_ACTION_MODES,/);
    assert.doesNotMatch(source,
        /Meta\.KeyBindingFlags\.IGNORE_AUTOREPEAT,\s*Shell\.ActionMode\.NORMAL,/);
});

test('install manifest is complete and contains only payload files', async () => {
    const manifest = JSON.parse(await read('install-manifest.json'));
    assert.equal(manifest.uuid, uuid);
    assert.deepEqual(manifest['default-keybinding'], ['<Super><Shift>s']);
    assert.deepEqual(manifest['managed-desktop-keybinding'], ['Print']);
    assert.equal(manifest['managed-desktop-conflicts'][0].path,
        '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/snipsnap/');
    assert.ok(manifest.files.includes('capture-lifecycle.js'));
    assert.ok(manifest.files.includes('capture-topology.js'));
    assert.ok(manifest.files.includes('handoff-client.js'));
    assert.ok(manifest.files.includes('handoff-protocol.js'));
    assert.ok(manifest.files.includes('handoff-state.js'));
    assert.ok(manifest.files.includes('selected-region.js'));

    for (const relativePath of manifest.files) {
        const contents = await readFile(
            path.join(extensionDirectory, relativePath));
        assert.ok(contents.length > 0, `${relativePath} must not be empty`);
        assert.doesNotMatch(relativePath, /(^|\/)tests?\//);
    }
});

test('schema default avoids racing GNOME built-in Print binding', async () => {
    const schema = await read(
        `${sourceDirectory}/schemas/org.gnome.shell.extensions.flameshot-shell-bridge.gschema.xml`);

    assert.match(schema, /name="show-capture-overlay" type="as"/);
    assert.match(schema, /&lt;Super&gt;&lt;Shift&gt;s/);
    assert.doesNotMatch(schema, /<default>\["Print"\]<\/default>/);
    // external-annotator must default OFF: a default-true regression puts
    // every non-managed install into a mode its daemon does not implement.
    assert.match(schema,
        /name="external-annotator" type="b">\s*<default>false<\/default>/);
});
