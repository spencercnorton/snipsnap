// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#include "capturetrace.h"
#include "config/cacheutils.h"

#include <QDir>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QMutex>
#include <QMutexLocker>
#include <QRandomGenerator>
#include <atomic>
#include <cstdio>

namespace CaptureTrace {
namespace {

struct TraceState
{
    QMutex mutex;
    QElapsedTimer timer;
    QElapsedTimer processTimer;
    QFile file;
    QString runId;
    QString generatedRunId;
    quint64 captureId{ 0 };
    qint64 elapsedBaseUs{ 0 };
    std::atomic_bool active{ false };
    bool processTimerStarted{ false };
    bool outputInitialized{ false };
    bool outputAvailable{ false };
    bool writeToFile{ false };
};

TraceState& state()
{
    static TraceState traceState;
    return traceState;
}

QByteArray normalizedSetting()
{
    return qgetenv("SNIPSNAP_CAPTURE_TRACE").trimmed().toLower();
}

bool settingIsEnabled(const QByteArray& setting)
{
    return !setting.isEmpty() && setting != "0" && setting != "false" &&
           setting != "off" && setting != "no";
}

void ensureProcessTimerStarted(TraceState& traceState)
{
    if (!traceState.processTimerStarted) {
        traceState.processTimer.start();
        traceState.processTimerStarted = true;
    }
}

qint64 processElapsedUs(const TraceState& traceState)
{
    return traceState.processTimer.nsecsElapsed() / 1000;
}

bool initializeOutput(TraceState& traceState, const QByteArray& setting)
{
    if (traceState.outputInitialized) {
        return traceState.outputAvailable;
    }
    traceState.outputInitialized = true;

    if (setting != "file") {
        traceState.outputAvailable = true;
        return true;
    }

#if defined(Q_OS_WIN)
    // QFile permissions do not provide a portable owner-only ACL guarantee on
    // Windows. Keep stderr tracing available, but refuse the private-file mode
    // instead of claiming a privacy property the platform cannot enforce.
    return false;
#else

    // getCachePath() creates or tightens the containing directory to 0700
    // before the append-only trace is opened. That avoids a creation-time
    // visibility window before the file itself can be tightened to 0600.
    const QString cacheDirectory = getCachePath();
    if (cacheDirectory.isEmpty()) {
        return false;
    }

    const QString filePath =
      QDir(cacheDirectory).filePath(QStringLiteral("capture-trace.jsonl"));
    const QFileInfo fileInfo(filePath);
    if (fileInfo.exists() && (fileInfo.isSymLink() || !fileInfo.isFile())) {
        return false;
    }

    traceState.file.setFileName(filePath);
    if (!traceState.file.open(QIODevice::WriteOnly | QIODevice::Append |
                              QIODevice::Text)) {
        return false;
    }
    if (!traceState.file.setPermissions(QFileDevice::ReadOwner |
                                        QFileDevice::WriteOwner)) {
        traceState.file.close();
        return false;
    }
    traceState.writeToFile = true;
    traceState.outputAvailable = true;
    return true;
#endif
}

bool writeRecord(TraceState& traceState, const Record& record, bool flush)
{
    QByteArray line = serialize(record);
    if (!traceState.writeToFile) {
        line.prepend(StderrPrefix);
    }
    line.append('\n');
    if (traceState.writeToFile) {
        if (traceState.file.write(line) != line.size()) {
            return false;
        }
        return !flush || traceState.file.flush();
    }

    const auto expected = static_cast<std::size_t>(line.size());
    if (std::fwrite(line.constData(), sizeof(char), expected, stderr) !=
        expected) {
        return false;
    }
    return !flush || std::fflush(stderr) == 0;
}

QString runId(TraceState& traceState)
{
    const QByteArray requested =
      qgetenv("SNIPSNAP_CAPTURE_TRACE_RUN_ID").trimmed();
    bool valid = !requested.isEmpty() && requested.size() <= 64;
    for (const char character : requested) {
        const bool allowed =
          (character >= 'a' && character <= 'z') ||
          (character >= 'A' && character <= 'Z') ||
          (character >= '0' && character <= '9') || character == '.' ||
          character == '_' || character == '-';
        if (!allowed) {
            valid = false;
            break;
        }
    }
    if (valid && requested != "__legacy__") {
        return QString::fromLatin1(requested);
    }
    if (traceState.generatedRunId.isEmpty()) {
        traceState.generatedRunId =
          QStringLiteral("process-%1")
            .arg(QRandomGenerator::system()->generate64(),
                 16,
                 16,
                 QLatin1Char('0'));
    }
    return traceState.generatedRunId;
}

Record currentRecord(const TraceState& traceState,
                     QStringView event,
                     const QJsonObject& fields)
{
    return { traceState.runId,
             traceState.captureId,
             event.toString(),
             traceState.elapsedBaseUs +
               traceState.timer.nsecsElapsed() / 1000,
             fields };
}

} // namespace

bool isEnabled()
{
    return settingIsEnabled(normalizedSetting());
}

void initializeProcessStart()
{
    TraceState& traceState = state();
    const QMutexLocker locker(&traceState.mutex);
    ensureProcessTimerStarted(traceState);
}

TimelineStart captureRequestStart()
{
    if (!isEnabled()) {
        return {};
    }
    TraceState& traceState = state();
    const QMutexLocker locker(&traceState.mutex);
    ensureProcessTimerStarted(traceState);
    return { processElapsedUs(traceState),
             TimelineOrigin::CaptureRequest,
             true };
}

TimelineStart processStart()
{
    if (!isEnabled()) {
        return {};
    }
    TraceState& traceState = state();
    const QMutexLocker locker(&traceState.mutex);
    ensureProcessTimerStarted(traceState);
    return { 0, TimelineOrigin::ProcessStart, true };
}

bool begin(QStringView source, const TimelineStart& start)
{
    const QByteArray setting = normalizedSetting();
    if (!start.valid || !settingIsEnabled(setting)) {
        return false;
    }

    TraceState& traceState = state();
    const QMutexLocker locker(&traceState.mutex);
    if (traceState.active.load()) {
        return false;
    }
    if (!initializeOutput(traceState, setting)) {
        traceState.active.store(false);
        return false;
    }
    // Each `snipsnap gui` invocation may run in a fresh process while all
    // records append to the same trace. Keep IDs JSON-exact and collision
    // resistant across those processes instead of using a process-local
    // counter. IEEE-754 represents every integer through 2^53 exactly.
    constexpr quint64 JsonExactIntegerMask = (quint64{ 1 } << 53) - 1;
    traceState.captureId =
      QRandomGenerator::system()->generate64() & JsonExactIntegerMask;
    if (traceState.captureId == 0) {
        traceState.captureId = 1;
    }
    traceState.timer.start();
    traceState.runId = runId(traceState);
    ensureProcessTimerStarted(traceState);
    const qint64 timerElapsedUs = traceState.timer.nsecsElapsed() / 1000;
    traceState.elapsedBaseUs =
      qMax<qint64>(0,
                   processElapsedUs(traceState) - start.processElapsedUs -
                     timerElapsedUs);
    traceState.active.store(true);
    const bool includesProcessStart =
      start.origin == TimelineOrigin::ProcessStart;
    if (includesProcessStart &&
        !writeRecord(traceState,
                     { traceState.runId,
                       traceState.captureId,
                       QStringLiteral("process_started"),
                       0,
                       {} },
                     false)) {
        traceState.active.store(false);
        return false;
    }
    if (!writeRecord(traceState,
                     { traceState.runId,
                       traceState.captureId,
                       QStringLiteral("capture_requested"),
                       0,
                       { { QStringLiteral("source"), source.toString() },
                         { QStringLiteral("origin"),
                           includesProcessStart
                             ? QStringLiteral("process_start")
                             : QStringLiteral("request_receipt") } } },
                     false)) {
        traceState.active.store(false);
        return false;
    }
    return true;
}

void mark(QStringView event, const QJsonObject& fields)
{
    TraceState& traceState = state();
    if (!traceState.active.load()) {
        return;
    }
    const QMutexLocker locker(&traceState.mutex);
    if (!traceState.active.load()) {
        return;
    }
    if (!writeRecord(
          traceState, currentRecord(traceState, event, fields), false)) {
        traceState.active.store(false);
    }
}

void finish(QStringView event, const QJsonObject& fields)
{
    TraceState& traceState = state();
    if (!traceState.active.load()) {
        return;
    }
    const QMutexLocker locker(&traceState.mutex);
    if (!traceState.active.load()) {
        return;
    }
    (void)writeRecord(
      traceState, currentRecord(traceState, event, fields), true);
    traceState.active.store(false);
}

QByteArray serialize(const Record& record)
{
    QJsonObject object;
    object.insert(QStringLiteral("schema"), QLatin1String(Schema));
    object.insert(QStringLiteral("run_id"), record.runId);
    object.insert(QStringLiteral("capture_id"),
                  static_cast<qint64>(record.captureId));
    object.insert(QStringLiteral("event"), record.event);
    object.insert(QStringLiteral("elapsed_us"), record.elapsedUs);
    if (!record.fields.isEmpty()) {
        object.insert(QStringLiteral("fields"), record.fields);
    }
    return QJsonDocument(object).toJson(QJsonDocument::Compact);
}

} // namespace CaptureTrace
