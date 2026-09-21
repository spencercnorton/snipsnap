// SPDX-License-Identifier: GPL-3.0-or-later

import Gio from 'gi://Gio';
import Shell from 'gi://Shell';

import {validateCapturedStage} from './capture-topology.js';
import {MAX_PNG_BYTES} from './handoff-protocol.js';
import {
    scaleToFraction,
    selectionToPixelRegion,
} from './handoff-state.js';

/**
 * Read back and PNG-encode only the selected texture rectangle.
 *
 * Shell.Screenshot.composite_to_stream() performs the texture readback before
 * its asynchronous PNG-stream completion. The pixel cap is checked first so
 * this synchronous section has a hard input bound.
 */
export async function encodeSelectedRegion(
    content, selection, scale, captureTopology) {
    if (!content || typeof content.get_texture !== 'function')
        throw new TypeError('captured stage content is unavailable');

    const texture = content.get_texture();
    if (!texture)
        throw new Error('captured stage texture is unavailable');

    const textureSize = {
        width: texture.get_width(),
        height: texture.get_height(),
    };
    validateCapturedStage(captureTopology, scale, textureSize);
    const pixelRegion = selectionToPixelRegion(
        selection, scale, textureSize, {
            width: captureTopology.stageWidth,
            height: captureTopology.stageHeight,
        });
    const stream = Gio.MemoryOutputStream.new_resizable();

    try {
        await Shell.Screenshot.composite_to_stream(
            texture,
            pixelRegion.x,
            pixelRegion.y,
            pixelRegion.width,
            pixelRegion.height,
            scale,
            null,
            0,
            0,
            1,
            stream
        );
        stream.close(null);

        const png = stream.steal_as_bytes().get_data();
        if (png.byteLength > MAX_PNG_BYTES)
            throw new RangeError('selected PNG exceeds handoff bounds');

        return {
            png,
            pixelRegion,
            scaleFraction: scaleToFraction(scale),
        };
    } catch (error) {
        if (!stream.is_closed())
            stream.close(null);
        throw error;
    }
}
