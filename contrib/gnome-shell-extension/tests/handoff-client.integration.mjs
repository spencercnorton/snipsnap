// SPDX-License-Identifier: GPL-3.0-or-later

import Gio from 'gi://Gio';

import {CommitAckIndeterminateError, handoffFrame} from
    '../snipsnap-shell-bridge/handoff-client.js';
import {AckStatus} from
    '../snipsnap-shell-bridge/handoff-protocol.js';

const scenario = ARGV[0];
const captureId = 0x0102030405060708n;
const cancellable = new Gio.Cancellable();

try {
    const result = await handoffFrame(
        Uint8Array.of(1, 2, 3),
        captureId,
        cancellable,
        () => {},
        async () => {},
        () => {}
    );
    if (scenario !== 'normal' || result.daemonPid <= 1)
        throw new Error(`unexpected successful result for ${scenario}`);
    print(`normal-ok pid=${result.daemonPid}`);
} catch (error) {
    const indeterminate =
        error instanceof CommitAckIndeterminateError &&
        error.commitSent === true;
    if (scenario === 'lost-final-ack' && indeterminate) {
        print(`${scenario}-indeterminate-ok`);
    } else if (scenario === 'terminal-final-ack' && indeterminate &&
               error.cause?.status === AckStatus.INTERNAL_ERROR) {
        print(`${scenario}-indeterminate-ok`);
    } else if (scenario === 'wrong-dbus-owner' &&
               String(error).includes(
                   'not the active SnipSnap D-Bus owner')) {
        print('wrong-dbus-owner-rejected-ok');
    } else {
        logError(error, `unexpected ${scenario} result`);
        throw error;
    }
}
