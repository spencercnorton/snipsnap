// SPDX-License-Identifier: GPL-3.0-or-later

export class CaptureLifecycle {
    constructor() {
        this._nextCaptureId = 0;
        this._lifecycleToken = null;
        this._activeRequest = null;
    }

    enable() {
        this._lifecycleToken = {};
        this._activeRequest = null;
    }

    disable() {
        this._lifecycleToken = null;
        this._activeRequest = null;
    }

    nextCaptureId() {
        return ++this._nextCaptureId;
    }

    get busy() {
        return this._activeRequest !== null;
    }

    beginRequest(captureId) {
        if (this._lifecycleToken === null || this._activeRequest !== null)
            return null;

        const request = Object.freeze({
            captureId,
            lifecycleToken: this._lifecycleToken,
        });
        this._activeRequest = request;
        return request;
    }

    invalidateRequest() {
        this._activeRequest = null;
    }

    isCurrent(request) {
        return request !== null &&
            this._lifecycleToken !== null &&
            request.lifecycleToken === this._lifecycleToken &&
            request === this._activeRequest;
    }

    finishRequest(request) {
        if (!this.isCurrent(request))
            return false;

        this._activeRequest = null;
        return true;
    }
}

export function unlockedActionModes(actionMode) {
    return actionMode.ALL & ~(
        actionMode.LOGIN_SCREEN |
        actionMode.LOCK_SCREEN |
        actionMode.UNLOCK_SCREEN
    );
}
