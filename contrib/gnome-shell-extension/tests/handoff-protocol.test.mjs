// SPDX-License-Identifier: GPL-3.0-or-later

import assert from 'node:assert/strict';
import test from 'node:test';

import {
    AckStatus,
    encodeCommit,
    encodeRequest,
    MAX_PNG_BYTES,
    parseAck,
} from '../snipsnap-shell-bridge/handoff-protocol.js';

function bytes(hex) {
    return Uint8Array.from(hex.match(/../g), value => Number.parseInt(value, 16));
}

function hex(value) {
    return Array.from(value, byte => byte.toString(16).padStart(2, '0')).join('');
}

const PNG = bytes(
    '89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c02' +
    '0000000b4944415478da6364f80f00010501012718e3660000000049454e44ae4260' +
    '82');

const GOLDEN_REQUEST =
    '46534252504e473100000040000000000102030405060708fffff880ffffff880000' +
    '000100000001000000010000000100000001000000010000000000000044' +
    hex(PNG);

const GOLDEN_ACCEPTED_ACK =
    '4653425241434b31010203040506070800000001000010920000000000000000';

function request(overrides = {}) {
    return {
        captureId: 0x0102030405060708n,
        logicalX: -1920,
        logicalY: -120,
        logicalWidth: 1,
        logicalHeight: 1,
        pixelWidth: 1,
        pixelHeight: 1,
        scaleNumerator: 1,
        scaleDenominator: 1,
        png: PNG,
        ...overrides,
    };
}

test('request encoder matches the shared big-endian golden vector', () => {
    assert.equal(hex(encodeRequest(request())), GOLDEN_REQUEST);
});

test('ACK parser matches the shared big-endian golden vector', () => {
    assert.deepEqual(parseAck(bytes(GOLDEN_ACCEPTED_ACK)), {
        captureId: 0x0102030405060708n,
        status: AckStatus.REQUEST_ACCEPTED,
        daemonPid: 4242,
    });
});

test('commit encoder matches the shared big-endian golden vector', () => {
    assert.equal(hex(encodeCommit(0x0102030405060708n)),
        '46534252434d54310102030405060708');
    assert.throws(() => encodeCommit(0), /nonzero/);
});

test('request encoder enforces dimensions, pixels, scale, and PNG bounds', () => {
    assert.throws(() => encodeRequest(request({captureId: 0})), /nonzero/);
    assert.throws(() => encodeRequest(request({logicalWidth: 0})), /bounds/);
    assert.throws(() => encodeRequest(request({pixelWidth: 32769})), /bounds/);
    assert.throws(() => encodeRequest(request({
        pixelWidth: 8000,
        pixelHeight: 4001,
    })), /pixel count/);
    assert.throws(() => encodeRequest(request({scaleDenominator: 0})), /bounds/);
    assert.throws(() => encodeRequest(request({
        scaleNumerator: 1,
        scaleDenominator: 3,
    })), /scale/);
    assert.throws(() => encodeRequest(request({scaleNumerator: 5})), /scale/);
    assert.throws(() => encodeRequest(request({
        scaleNumerator: 1_000_001,
        scaleDenominator: 1_000_000,
    })), /bounds/);
    assert.throws(() => encodeRequest(request({
        logicalX: 0x7fffffff,
        logicalWidth: 2,
        pixelWidth: 2,
        png: PNG,
    })), /overflows/);
    assert.throws(() => encodeRequest(request({
        logicalWidth: 3,
    })), /contradict/);
    assert.throws(() => encodeRequest(request({
        png: new Uint8Array(MAX_PNG_BYTES + 1),
    })), /PNG length/);
});

test('request encoder rejects invalid PNG identity and dimensions', () => {
    const invalidSignature = PNG.slice();
    invalidSignature[0] = 0;
    assert.throws(() => encodeRequest(request({png: invalidSignature})),
        /signature/);

    const invalidIhdr = PNG.slice();
    invalidIhdr[12] = 0;
    assert.throws(() => encodeRequest(request({png: invalidIhdr})), /IHDR/);

    assert.throws(() => encodeRequest(request({
        logicalWidth: 2,
        pixelWidth: 2,
    })),
        /dimensions/);

    const interlaced = PNG.slice();
    interlaced[28] = 1;
    assert.throws(() => encodeRequest(request({png: interlaced})),
        /non-interlaced/);
});

test('ACK parser rejects reserved, unknown, truncated, and trailing data', () => {
    const reserved = bytes(GOLDEN_ACCEPTED_ACK);
    reserved[31] = 1;
    assert.throws(() => parseAck(reserved), /reserved/);

    const unknown = bytes(GOLDEN_ACCEPTED_ACK);
    unknown.set([0, 0, 0, 99], 16);
    assert.throws(() => parseAck(unknown), /status/);
    assert.throws(() => parseAck(bytes(GOLDEN_ACCEPTED_ACK).slice(0, 31)),
        /exactly/);

    const trailing = new Uint8Array(33);
    trailing.set(bytes(GOLDEN_ACCEPTED_ACK));
    assert.throws(() => parseAck(trailing), /exactly/);
});
