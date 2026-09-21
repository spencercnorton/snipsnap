// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#pragma once

#include <QByteArray>
#include <QString>
#include <QtGlobal>

namespace ShellBridgeProtocol
{

inline constexpr qsizetype RequestHeaderSize = 64;
inline constexpr qsizetype AckSize = 32;
inline constexpr qsizetype CommitSize = 16;
inline constexpr quint32 MaxDimension = 32'768;
inline constexpr quint64 MaxPixelCount = 32'000'000;
inline constexpr quint64 MaxPngBytes = 128ULL * 1024ULL * 1024ULL;

struct Request
{
    quint64 captureId{ 0 };
    qint32 logicalX{ 0 };
    qint32 logicalY{ 0 };
    quint32 logicalWidth{ 0 };
    quint32 logicalHeight{ 0 };
    quint32 pixelWidth{ 0 };
    quint32 pixelHeight{ 0 };
    quint32 scaleNumerator{ 0 };
    quint32 scaleDenominator{ 0 };
    quint64 payloadLength{ 0 };
    QByteArray png;
};

enum class ParseStatus
{
    Incomplete,
    Complete,
    Error,
};

class RequestParser final
{
public:
    ParseStatus append(const QByteArray& bytes);
    void reset();

    ParseStatus status() const;
    QString errorString() const;
    const Request& request() const;

private:
    ParseStatus fail(const QString& message);
    bool parseHeader();
    bool validatePngPrefix();
    bool validatePngStructure();

    QByteArray m_header;
    Request m_request;
    ParseStatus m_status{ ParseStatus::Incomplete };
    QString m_errorString;
    bool m_headerParsed{ false };
    bool m_pngPrefixValidated{ false };
};

enum class AckStatus : quint32
{
    RequestAccepted = 1,
    EditorReady = 2,
    CommitAccepted = 3,
    MalformedRequest = 0x80000001U,
    Busy = 0x80000002U,
    DecodeFailed = 0x80000003U,
    InternalError = 0x80000004U,
    Timeout = 0x80000005U,
};

struct Ack
{
    quint64 captureId{ 0 };
    AckStatus status{ AckStatus::InternalError };
    quint32 daemonPid{ 0 };
};

QByteArray encodeAck(const Ack& ack);
bool decodeAck(const QByteArray& bytes,
               Ack* ack,
               QString* errorString = nullptr);
bool isKnownAckStatus(quint32 status);
QByteArray encodeCommit(quint64 captureId);
bool decodeCommit(const QByteArray& bytes,
                  quint64* captureId,
                  QString* errorString = nullptr);

} // namespace ShellBridgeProtocol
