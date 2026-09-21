// SPDX-License-Identifier: GPL-3.0-or-later

export const REQUEST_HEADER_SIZE = 64;
export const ACK_SIZE = 32;
export const COMMIT_SIZE = 16;
export const MAX_DIMENSION = 32_768;
export const MAX_PIXEL_COUNT = 32_000_000;
export const MAX_PNG_BYTES = 128 * 1024 * 1024;
export const MAX_SCALE_COMPONENT = 1_000_000;

export const AckStatus = Object.freeze({
    REQUEST_ACCEPTED: 1,
    EDITOR_READY: 2,
    COMMIT_ACCEPTED: 3,
    MALFORMED_REQUEST: 0x80000001,
    BUSY: 0x80000002,
    DECODE_FAILED: 0x80000003,
    INTERNAL_ERROR: 0x80000004,
    TIMEOUT: 0x80000005,
});

const REQUEST_MAGIC = Uint8Array.of(
    0x46, 0x53, 0x42, 0x52, 0x50, 0x4e, 0x47, 0x31);
const ACK_MAGIC = Uint8Array.of(
    0x46, 0x53, 0x42, 0x52, 0x41, 0x43, 0x4b, 0x31);
const COMMIT_MAGIC = Uint8Array.of(
    0x46, 0x53, 0x42, 0x52, 0x43, 0x4d, 0x54, 0x31);
const PNG_SIGNATURE = Uint8Array.of(
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a);
const KNOWN_ACK_STATUSES = new Set(Object.values(AckStatus));

function asBytes(value, name) {
    if (value instanceof Uint8Array)
        return value;
    if (value instanceof ArrayBuffer)
        return new Uint8Array(value);
    throw new TypeError(`${name} must be a Uint8Array or ArrayBuffer`);
}

function requireInteger(value, minimum, maximum, name) {
    if (!Number.isInteger(value) || value < minimum || value > maximum)
        throw new RangeError(`${name} is outside protocol bounds`);
    return value;
}

function requireUint64(value, name) {
    const result = typeof value === 'bigint'
        ? value
        : BigInt(requireInteger(value, 0, Number.MAX_SAFE_INTEGER, name));
    if (result < 0n || result > 0xffffffffffffffffn)
        throw new RangeError(`${name} is outside uint64 bounds`);
    return result;
}

function hasPrefix(bytes, expected) {
    return expected.every((value, index) => bytes[index] === value);
}

function validatePng(png, pixelWidth, pixelHeight) {
    if (png.byteLength < 33 || png.byteLength > MAX_PNG_BYTES)
        throw new RangeError('PNG length is outside protocol bounds');
    if (!hasPrefix(png, PNG_SIGNATURE))
        throw new Error('PNG signature is invalid');

    const view = new DataView(png.buffer, png.byteOffset, png.byteLength);
    if (view.getUint32(8) !== 13 ||
        String.fromCharCode(...png.subarray(12, 16)) !== 'IHDR')
        throw new Error('PNG IHDR is invalid');
    if (view.getUint32(16) !== pixelWidth ||
        view.getUint32(20) !== pixelHeight)
        throw new Error('PNG dimensions do not match the request');
    const colorType = png[25];
    if (png[24] !== 8 || ![0, 2, 4, 6].includes(colorType) ||
        png[26] !== 0 || png[27] !== 0 || png[28] !== 0)
        throw new Error('PNG must be a non-interlaced 8-bit static image');
}

function floorDivide(numerator, denominator) {
    let quotient = numerator / denominator;
    if (numerator % denominator < 0n)
        quotient--;
    return quotient;
}

function ceilDivide(numerator, denominator) {
    let quotient = numerator / denominator;
    if (numerator % denominator > 0n)
        quotient++;
    return quotient;
}

function mappingMatches(logicalOrigin, logicalSize, pixelSize,
    scaleNumerator, scaleDenominator) {
    const origin = BigInt(logicalOrigin);
    const logicalEnd = origin + BigInt(logicalSize);
    const numerator = BigInt(scaleNumerator);
    const denominator = BigInt(scaleDenominator);
    const pixelStart = floorDivide(origin * numerator, denominator);
    const pixelEnd = ceilDivide(logicalEnd * numerator, denominator);
    const expected = pixelEnd - pixelStart;
    const difference = expected - BigInt(pixelSize);
    return expected > 0n && difference >= -1n && difference <= 1n;
}

export function encodeRequest(request) {
    const captureId = requireUint64(request.captureId, 'captureId');
    if (captureId === 0n)
        throw new RangeError('captureId must be nonzero');
    const logicalX = requireInteger(
        request.logicalX, -0x80000000, 0x7fffffff, 'logicalX');
    const logicalY = requireInteger(
        request.logicalY, -0x80000000, 0x7fffffff, 'logicalY');
    const logicalWidth = requireInteger(
        request.logicalWidth, 1, MAX_DIMENSION, 'logicalWidth');
    const logicalHeight = requireInteger(
        request.logicalHeight, 1, MAX_DIMENSION, 'logicalHeight');
    if (logicalX + logicalWidth - 1 > 0x7fffffff ||
        logicalY + logicalHeight - 1 > 0x7fffffff)
        throw new RangeError('logical rectangle overflows coordinates');
    const pixelWidth = requireInteger(
        request.pixelWidth, 1, MAX_DIMENSION, 'pixelWidth');
    const pixelHeight = requireInteger(
        request.pixelHeight, 1, MAX_DIMENSION, 'pixelHeight');
    if (pixelWidth * pixelHeight > MAX_PIXEL_COUNT)
        throw new RangeError('pixel count exceeds protocol bounds');

    const scaleNumerator = requireInteger(
        request.scaleNumerator, 1, MAX_SCALE_COMPONENT, 'scaleNumerator');
    const scaleDenominator = requireInteger(
        request.scaleDenominator, 1, MAX_SCALE_COMPONENT, 'scaleDenominator');
    const wideScaleNumerator = BigInt(scaleNumerator);
    const wideScaleDenominator = BigInt(scaleDenominator);
    if (wideScaleNumerator * 2n < wideScaleDenominator ||
        wideScaleNumerator > wideScaleDenominator * 4n)
        throw new RangeError('scale is outside protocol bounds');
    if (!mappingMatches(logicalX, logicalWidth, pixelWidth,
        scaleNumerator, scaleDenominator) ||
        !mappingMatches(logicalY, logicalHeight, pixelHeight,
            scaleNumerator, scaleDenominator))
        throw new RangeError(
            'pixel dimensions contradict logical rectangle and scale');

    const png = asBytes(request.png, 'png');
    validatePng(png, pixelWidth, pixelHeight);

    const frame = new Uint8Array(REQUEST_HEADER_SIZE + png.byteLength);
    frame.set(REQUEST_MAGIC);
    const view = new DataView(frame.buffer);
    view.setUint32(8, REQUEST_HEADER_SIZE);
    view.setUint32(12, 0);
    view.setBigUint64(16, captureId);
    view.setInt32(24, logicalX);
    view.setInt32(28, logicalY);
    view.setUint32(32, logicalWidth);
    view.setUint32(36, logicalHeight);
    view.setUint32(40, pixelWidth);
    view.setUint32(44, pixelHeight);
    view.setUint32(48, scaleNumerator);
    view.setUint32(52, scaleDenominator);
    view.setBigUint64(56, BigInt(png.byteLength));
    frame.set(png, REQUEST_HEADER_SIZE);
    return frame;
}

export function parseAck(value) {
    const bytes = asBytes(value, 'ACK');
    if (bytes.byteLength !== ACK_SIZE)
        throw new RangeError('ACK must be exactly 32 bytes');
    if (!hasPrefix(bytes, ACK_MAGIC))
        throw new Error('ACK magic is invalid');

    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const status = view.getUint32(16);
    if (!KNOWN_ACK_STATUSES.has(status))
        throw new Error('ACK status is invalid');
    if (view.getBigUint64(24) !== 0n)
        throw new Error('ACK reserved field must be zero');

    return {
        captureId: view.getBigUint64(8),
        status,
        daemonPid: view.getUint32(20),
    };
}

export function encodeCommit(captureIdValue) {
    const captureId = requireUint64(captureIdValue, 'captureId');
    if (captureId === 0n)
        throw new RangeError('captureId must be nonzero');
    const frame = new Uint8Array(COMMIT_SIZE);
    frame.set(COMMIT_MAGIC);
    new DataView(frame.buffer).setBigUint64(8, captureId);
    return frame;
}
