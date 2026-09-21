// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#pragma once

#include <QByteArray>
#include <QJsonObject>
#include <QString>
#include <QStringView>
#include <QtGlobal>

namespace CaptureTrace {

inline constexpr auto Schema = "snipsnap.capture-trace.v1";
inline constexpr auto StderrPrefix = "SNIPSNAP_CAPTURE_TRACE ";

struct Record
{
    QString runId;
    quint64 captureId{ 0 };
    QString event;
    qint64 elapsedUs{ 0 };
    QJsonObject fields;
};

enum class TimelineOrigin
{
    CaptureRequest,
    ProcessStart,
};

struct TimelineStart
{
    qint64 processElapsedUs{ 0 };
    TimelineOrigin origin{ TimelineOrigin::CaptureRequest };
    bool valid{ false };
};

// Tracing is deliberately opt-in. Set SNIPSNAP_CAPTURE_TRACE to a truthy
// value for prefixed JSONL on stderr, or to "file" for a private file in the
// user's standard cache directory on Unix.
bool isEnabled();

// Call this as early as possible in main(). It performs no I/O and lets a
// cold CLI capture include application startup in its monotonic timeline.
void initializeProcessStart();

// Capture an origin without taking ownership of the global trace. This lets a
// request measure reservation/configuration work before begin() safely claims
// the timeline, without disturbing an already-active request.
TimelineStart captureRequestStart();
TimelineStart processStart();

// Starts a new graphical-capture timeline and writes capture_requested.
// Source and all event fields must be bounded diagnostic values. Never pass
// screenshot bytes, paths, URIs, window titles, or clipboard contents.
// Returns false when tracing is disabled, output cannot be secured, or an
// existing capture already owns the trace. A rejected reentrant request must
// never replace or terminate the existing capture's timeline.
bool begin(QStringView source, const TimelineStart& start);
void mark(QStringView event, const QJsonObject& fields = {});
void finish(QStringView event, const QJsonObject& fields = {});

// Exposed so the JSONL contract can be verified without enabling trace I/O.
QByteArray serialize(const Record& record);

} // namespace CaptureTrace
