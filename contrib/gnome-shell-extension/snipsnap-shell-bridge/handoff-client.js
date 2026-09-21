// SPDX-License-Identifier: GPL-3.0-or-later

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {
    ACK_SIZE,
    AckStatus,
    encodeCommit,
    parseAck,
} from './handoff-protocol.js';

const SOCKET_DIRECTORY = 'snipsnap';
const SOCKET_NAME = 'gnome-shell-bridge-v1.sock';
const SNIPSNAP_DBUS_SERVICE = 'tech.norvi.snipsnap';
const REQUEST_TIMEOUT_MS = 6_000;
const EDITOR_READY_TIMEOUT_MS = 7_000;
const COMMIT_TIMEOUT_MS = 3_500;
const WRITE_CHUNK_SIZE = 1024 * 1024;
const DBUS_START_REPLY_SUCCESS = 1;
const DBUS_START_REPLY_ALREADY_RUNNING = 2;
const SERVICE_START_RETRY_DELAY_MS = 250;
const SERVICE_START_CONNECT_ATTEMPTS = 4;

const TERMINAL_STATUS_NAMES = new Map([
    [AckStatus.MALFORMED_REQUEST, 'malformed request'],
    [AckStatus.BUSY, 'daemon busy'],
    [AckStatus.DECODE_FAILED, 'PNG decode failed'],
    [AckStatus.INTERNAL_ERROR, 'daemon internal error'],
    [AckStatus.TIMEOUT, 'daemon timeout'],
]);

export class HandoffError extends Error {
    constructor(message, status = null) {
        super(message);
        this.name = 'HandoffError';
        this.status = status;
    }
}

export class CommitAckIndeterminateError extends HandoffError {
    constructor(cause) {
        const message = String(cause?.message ?? cause);
        super(
            `commit transmission began but its outcome is indeterminate: ${message}`);
        this.name = 'CommitAckIndeterminateError';
        this.commitSent = true;
        this.cause = cause;
    }
}

class HandoffTransportClosedError extends HandoffError {
    constructor(message) {
        super(message);
    }
}

function socketPath() {
    const runtimeDirectory = GLib.get_user_runtime_dir();
    if (!runtimeDirectory || !GLib.path_is_absolute(runtimeDirectory))
        throw new HandoffError('user runtime directory is unavailable');

    return GLib.build_filenamev([
        runtimeDirectory,
        SOCKET_DIRECTORY,
        SOCKET_NAME,
    ]);
}

function connectAsync(client, address, cancellable) {
    return new Promise((resolve, reject) => {
        client.connect_async(address, cancellable, (source, result) => {
            try {
                resolve(source.connect_finish(result));
            } catch (error) {
                reject(error);
            }
        });
    });
}

function writeBytesAsync(stream, bytes, cancellable) {
    return new Promise((resolve, reject) => {
        stream.write_bytes_async(
            new GLib.Bytes(bytes),
            GLib.PRIORITY_DEFAULT,
            cancellable,
            (source, result) => {
                try {
                    resolve(source.write_bytes_finish(result));
                } catch (error) {
                    reject(error);
                }
            }
        );
    });
}

async function writeAll(stream, frame, cancellable) {
    let offset = 0;
    while (offset < frame.byteLength) {
        const end = Math.min(offset + WRITE_CHUNK_SIZE, frame.byteLength);
        const written = await writeBytesAsync(
            stream, frame.subarray(offset, end), cancellable);
        if (!Number.isInteger(written) || written <= 0 || written > end - offset)
            throw new HandoffError('short or invalid daemon socket write');
        offset += written;
    }
}

function readBytesAsync(stream, count, cancellable) {
    return new Promise((resolve, reject) => {
        stream.read_bytes_async(
            count,
            GLib.PRIORITY_DEFAULT,
            cancellable,
            (source, result) => {
                try {
                    resolve(source.read_bytes_finish(result).get_data());
                } catch (error) {
                    reject(error);
                }
            }
        );
    });
}

async function readExact(stream, count, cancellable) {
    const result = new Uint8Array(count);
    let offset = 0;
    while (offset < count) {
        const bytes = await readBytesAsync(stream, count - offset, cancellable);
        if (bytes.byteLength === 0)
            throw new HandoffTransportClosedError(
                'daemon closed the socket before its ACK');
        result.set(bytes, offset);
        offset += bytes.byteLength;
    }
    return result;
}

function peerIdentity(connection) {
    const credentials = connection.get_socket().get_credentials();
    if (!credentials)
        throw new HandoffError('daemon socket has no peer credentials');

    const ownUser = Number(new Gio.Credentials().get_unix_user());
    const peerUser = Number(credentials.get_unix_user());
    const peerPid = Number(credentials.get_unix_pid());
    if (!Number.isInteger(ownUser) ||
        !Number.isInteger(peerUser) ||
        peerUser !== ownUser) {
        throw new HandoffError('daemon socket peer is not the current user');
    }
    if (!Number.isInteger(peerPid) || peerPid <= 0 || peerPid > 0xffffffff)
        throw new HandoffError('daemon socket peer PID is invalid');

    return {peerPid};
}

function snipsnapServicePid(cancellable) {
    return new Promise((resolve, reject) => {
        Gio.DBus.session.call(
            'org.freedesktop.DBus',
            '/org/freedesktop/DBus',
            'org.freedesktop.DBus',
            'GetConnectionUnixProcessID',
            new GLib.Variant('(s)', [SNIPSNAP_DBUS_SERVICE]),
            new GLib.VariantType('(u)'),
            Gio.DBusCallFlags.NONE,
            1_000,
            cancellable,
            (connection, result) => {
                try {
                    const [pid] = connection.call_finish(result).deepUnpack();
                    const servicePid = Number(pid);
                    if (!Number.isInteger(servicePid) || servicePid <= 0 ||
                        servicePid > 0xffffffff) {
                        throw new HandoffError(
                            'SnipSnap D-Bus owner PID is invalid');
                    }
                    resolve(servicePid);
                } catch (error) {
                    reject(error instanceof HandoffError
                        ? error
                        : new HandoffError(
                            `cannot authenticate SnipSnap D-Bus owner: ${error}`));
                }
            }
        );
    });
}

function isDaemonAbsentError(error) {
    return error?.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.NOT_FOUND) ||
        error?.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.CONNECTION_REFUSED);
}

function startSnipSnapService(cancellable) {
    return new Promise((resolve, reject) => {
        Gio.DBus.session.call(
            'org.freedesktop.DBus',
            '/org/freedesktop/DBus',
            'org.freedesktop.DBus',
            'StartServiceByName',
            new GLib.Variant('(su)', [SNIPSNAP_DBUS_SERVICE, 0]),
            new GLib.VariantType('(u)'),
            Gio.DBusCallFlags.NONE,
            REQUEST_TIMEOUT_MS,
            cancellable,
            (connection, result) => {
                try {
                    const [reply] = connection.call_finish(result).deepUnpack();
                    const code = Number(reply);
                    if (code !== DBUS_START_REPLY_SUCCESS &&
                        code !== DBUS_START_REPLY_ALREADY_RUNNING) {
                        throw new HandoffError(
                            `SnipSnap D-Bus activation returned ${code}`);
                    }
                    resolve(code);
                } catch (error) {
                    reject(error instanceof HandoffError
                        ? error
                        : new HandoffError(
                            `cannot start the SnipSnap service: ${error}`));
                }
            }
        );
    });
}

function sleepMs(milliseconds) {
    return new Promise(resolve => {
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, milliseconds, () => {
            resolve();
            return GLib.SOURCE_REMOVE;
        });
    });
}

async function connectWithActivation(client, address, cancellable) {
    let lastError = null;
    try {
        return await connectAsync(client, address, cancellable);
    } catch (error) {
        if (!isDaemonAbsentError(error))
            throw error;
        lastError = error;
    }
    // No receiver is listening (missing socket or a stale one). Ask D-Bus to
    // activate the shipped tech.norvi.snipsnap service: the daemon binds
    // the bridge socket before owning the bus name, so a successful start
    // implies the socket exists. This closes the daemon-not-running failure
    // (2026-07-22 reference-desktop incident) without spawning anything
    // ourselves.
    await startSnipSnapService(cancellable);
    for (let attempt = 0; attempt < SERVICE_START_CONNECT_ATTEMPTS; attempt++) {
        if (cancellable.is_cancelled())
            break;
        try {
            return await connectAsync(client, address, cancellable);
        } catch (error) {
            if (!isDaemonAbsentError(error))
                throw error;
            lastError = error;
        }
        if (attempt + 1 < SERVICE_START_CONNECT_ATTEMPTS)
            await sleepMs(SERVICE_START_RETRY_DELAY_MS);
    }
    throw new HandoffError(
        'SnipSnap daemon is not running and could not be started' +
        (lastError ? `: ${lastError.message}` : ''));
}

function requireAck(bytes, captureId, expectedStatus, peerPid) {
    const ack = parseAck(bytes);
    if (ack.daemonPid !== peerPid)
        throw new HandoffError('daemon ACK PID does not match its socket peer');
    const terminalName = TERMINAL_STATUS_NAMES.get(ack.status);
    if (terminalName)
        throw new HandoffError(terminalName, ack.status);
    if (ack.captureId !== captureId)
        throw new HandoffError('daemon ACK capture ID does not match');
    if (ack.status !== expectedStatus)
        throw new HandoffError('daemon ACK arrived out of order', ack.status);
    return ack;
}

function closeAsync(connection) {
    return new Promise(resolve => {
        connection.close_async(
            GLib.PRIORITY_DEFAULT,
            null,
            (source, result) => {
                try {
                    source.close_finish(result);
                } catch (_error) {
                    // The protocol result is already known; close errors are
                    // not actionable and must not affect a later capture.
                }
                resolve();
            }
        );
    });
}

/**
 * Send one pre-framed request to the warm SnipSnap daemon and wait until its
 * editor has painted. The caller owns cancellation and request identity.
 */
export async function handoffFrame(
    frame,
    captureId,
    cancellable,
    onPeerAuthenticated,
    onEditorReady,
    onCommitStarted) {
    if (!(frame instanceof Uint8Array))
        throw new TypeError('handoff frame must be a Uint8Array');
    if (typeof captureId !== 'bigint' || captureId <= 0n)
        throw new TypeError('capture ID must be a positive bigint');
    if (!(cancellable instanceof Gio.Cancellable))
        throw new TypeError('handoff cancellable is required');
    for (const [callback, name] of [
        [onPeerAuthenticated, 'peer-authenticated'],
        [onEditorReady, 'editor-ready'],
        [onCommitStarted, 'commit-started'],
    ]) {
        if (typeof callback !== 'function')
            throw new TypeError(`${name} callback must be a function`);
    }

    const client = new Gio.SocketClient();
    client.set_enable_proxy(false);
    client.set_timeout(Math.ceil(EDITOR_READY_TIMEOUT_MS / 1000));
    const address = Gio.UnixSocketAddress.new(socketPath());
    let connection = null;
    let timedOutPhase = null;
    let commitStarted = false;
    let timeoutId = 0;
    const armTimeout = (milliseconds, phase) => {
        if (timeoutId)
            GLib.source_remove(timeoutId);
        timeoutId = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT,
            milliseconds,
            () => {
                timeoutId = 0;
                timedOutPhase = phase;
                cancellable.cancel();
                return GLib.SOURCE_REMOVE;
            }
        );
    };

    try {
        armTimeout(REQUEST_TIMEOUT_MS, 'request');
        connection = await connectWithActivation(client, address, cancellable);
        const {peerPid} = peerIdentity(connection);
        const servicePid = await snipsnapServicePid(cancellable);
        if (servicePid !== peerPid) {
            throw new HandoffError(
                'daemon socket peer is not the active SnipSnap D-Bus owner');
        }
        onPeerAuthenticated(peerPid);
        await writeAll(connection.get_output_stream(), frame, cancellable);

        const captureAccepted = await readExact(
            connection.get_input_stream(), ACK_SIZE, cancellable);
        requireAck(
            captureAccepted,
            captureId,
            AckStatus.REQUEST_ACCEPTED,
            peerPid
        );
        armTimeout(EDITOR_READY_TIMEOUT_MS, 'editor-ready');

        const editorReady = await readExact(
            connection.get_input_stream(), ACK_SIZE, cancellable);
        requireAck(
            editorReady,
            captureId,
            AckStatus.EDITOR_READY,
            peerPid
        );
        armTimeout(COMMIT_TIMEOUT_MS, 'commit');

        await onEditorReady(peerPid);
        if (cancellable.is_cancelled())
            throw new HandoffError('handoff cancelled before commit');

        const commit = encodeCommit(captureId);
        onCommitStarted(peerPid);
        // From the first asynchronous write onward, no client-side error can
        // prove that the daemon did not receive the complete commit frame.
        // Conservatively finalize instead of offering a duplicate retry.
        commitStarted = true;
        await writeAll(connection.get_output_stream(), commit, cancellable);
        const commitAccepted = await readExact(
            connection.get_input_stream(), ACK_SIZE, cancellable);
        requireAck(
            commitAccepted,
            captureId,
            AckStatus.COMMIT_ACCEPTED,
            peerPid
        );
        return {daemonPid: peerPid};
    } catch (error) {
        if (commitStarted)
            throw new CommitAckIndeterminateError(error);
        if (timedOutPhase !== null) {
            throw new HandoffError(
                `daemon ${timedOutPhase} phase timed out`, AckStatus.TIMEOUT);
        }
        throw error;
    } finally {
        if (timeoutId)
            GLib.source_remove(timeoutId);
        if (connection)
            await closeAsync(connection);
    }
}
