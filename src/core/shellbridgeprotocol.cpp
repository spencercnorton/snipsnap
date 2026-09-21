// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#include "shellbridgeprotocol.h"

#include <QtEndian>

#include <algorithm>
#include <limits>
#include <utility>

namespace
{

constexpr char RequestMagic[] = "FSBRPNG1";
constexpr char AckMagic[] = "FSBRACK1";
constexpr char CommitMagic[] = "FSBRCMT1";
constexpr unsigned char PngSignature[] = {
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
};
constexpr qsizetype PngPrefixSize = 33;
constexpr quint64 MaxAncillaryBytes = 4 * 1024;
constexpr quint32 MaxPngChunks = 1024;
constexpr quint32 MaxScaleComponent = 1'000'000;

quint32 readU32(const QByteArray& bytes, qsizetype offset)
{
    return qFromBigEndian<quint32>(
      reinterpret_cast<const uchar*>(bytes.constData() + offset));
}

qint32 readI32(const QByteArray& bytes, qsizetype offset)
{
    return qFromBigEndian<qint32>(
      reinterpret_cast<const uchar*>(bytes.constData() + offset));
}

quint64 readU64(const QByteArray& bytes, qsizetype offset)
{
    return qFromBigEndian<quint64>(
      reinterpret_cast<const uchar*>(bytes.constData() + offset));
}

void writeU32(QByteArray& bytes, qsizetype offset, quint32 value)
{
    qToBigEndian<quint32>(
      value, reinterpret_cast<uchar*>(bytes.data() + offset));
}

void writeU64(QByteArray& bytes, qsizetype offset, quint64 value)
{
    qToBigEndian<quint64>(
      value, reinterpret_cast<uchar*>(bytes.data() + offset));
}

bool validDimension(quint32 value)
{
    return value > 0 && value <= ShellBridgeProtocol::MaxDimension;
}

bool validScale(quint32 numerator, quint32 denominator)
{
    if (numerator == 0 || denominator == 0
        || numerator > MaxScaleComponent || denominator > MaxScaleComponent) {
        return false;
    }

    const quint64 wideNumerator = numerator;
    const quint64 wideDenominator = denominator;
    return wideNumerator * 2 >= wideDenominator
      && wideNumerator <= wideDenominator * 4;
}

qint64 floorDivide(qint64 numerator, qint64 denominator)
{
    qint64 quotient = numerator / denominator;
    if (numerator % denominator < 0) {
        --quotient;
    }
    return quotient;
}

qint64 ceilDivide(qint64 numerator, qint64 denominator)
{
    qint64 quotient = numerator / denominator;
    if (numerator % denominator > 0) {
        ++quotient;
    }
    return quotient;
}

bool mappingMatches(qint32 logicalOrigin,
                    quint32 logicalSize,
                    quint32 pixelSize,
                    quint32 scaleNumerator,
                    quint32 scaleDenominator)
{
    const qint64 origin = logicalOrigin;
    const qint64 logicalEnd = origin + logicalSize;
    const qint64 pixelStart = floorDivide(
      origin * scaleNumerator, scaleDenominator);
    const qint64 pixelEnd = ceilDivide(
      logicalEnd * scaleNumerator, scaleDenominator);
    const qint64 expected = pixelEnd - pixelStart;
    return expected > 0
      && qAbs(expected - static_cast<qint64>(pixelSize)) <= 1;
}

} // namespace

namespace ShellBridgeProtocol
{

ParseStatus RequestParser::append(const QByteArray& bytes)
{
    if (m_status == ParseStatus::Error || bytes.isEmpty()) {
        return m_status;
    }
    if (m_status == ParseStatus::Complete) {
        return fail(QStringLiteral("request contains trailing bytes"));
    }

    qsizetype offset = 0;
    if (!m_headerParsed) {
        const qsizetype headerRemaining = RequestHeaderSize - m_header.size();
        const qsizetype amount = std::min(headerRemaining, bytes.size());
        m_header.append(bytes.constData(), amount);
        offset += amount;

        if (m_header.size() < RequestHeaderSize) {
            return m_status;
        }
        if (!parseHeader()) {
            return m_status;
        }
    }

    const qsizetype available = bytes.size() - offset;
    const quint64 received = static_cast<quint64>(m_request.png.size());
    const quint64 remaining = m_request.payloadLength - received;
    if (static_cast<quint64>(available) > remaining) {
        return fail(QStringLiteral("request contains trailing bytes"));
    }
    if (available > 0) {
        m_request.png.append(bytes.constData() + offset, available);
    }

    if (!validatePngPrefix()) {
        return m_status;
    }
    if (static_cast<quint64>(m_request.png.size())
        == m_request.payloadLength) {
        if (!m_pngPrefixValidated) {
            return fail(QStringLiteral("PNG payload is missing its IHDR prefix"));
        }
        if (!validatePngStructure()) {
            return m_status;
        }
        m_status = ParseStatus::Complete;
    }
    return m_status;
}

void RequestParser::reset()
{
    m_header.clear();
    m_request = Request{};
    m_status = ParseStatus::Incomplete;
    m_errorString.clear();
    m_headerParsed = false;
    m_pngPrefixValidated = false;
}

ParseStatus RequestParser::status() const
{
    return m_status;
}

QString RequestParser::errorString() const
{
    return m_errorString;
}

const Request& RequestParser::request() const
{
    return m_request;
}

ParseStatus RequestParser::fail(const QString& message)
{
    m_status = ParseStatus::Error;
    m_errorString = message;
    return m_status;
}

bool RequestParser::parseHeader()
{
    if (m_header.left(8) != QByteArray(RequestMagic, 8)) {
        fail(QStringLiteral("request magic is invalid"));
        return false;
    }
    if (readU32(m_header, 8) != static_cast<quint32>(RequestHeaderSize)) {
        fail(QStringLiteral("request header size is invalid"));
        return false;
    }
    if (readU32(m_header, 12) != 0) {
        fail(QStringLiteral("request flags must be zero"));
        return false;
    }

    Request request;
    request.captureId = readU64(m_header, 16);
    request.logicalX = readI32(m_header, 24);
    request.logicalY = readI32(m_header, 28);
    request.logicalWidth = readU32(m_header, 32);
    request.logicalHeight = readU32(m_header, 36);
    request.pixelWidth = readU32(m_header, 40);
    request.pixelHeight = readU32(m_header, 44);
    request.scaleNumerator = readU32(m_header, 48);
    request.scaleDenominator = readU32(m_header, 52);
    request.payloadLength = readU64(m_header, 56);

    if (request.captureId == 0) {
        fail(QStringLiteral("request capture ID must be nonzero"));
        return false;
    }
    if (!validDimension(request.logicalWidth)
        || !validDimension(request.logicalHeight)
        || !validDimension(request.pixelWidth)
        || !validDimension(request.pixelHeight)) {
        fail(QStringLiteral("request dimensions are outside protocol bounds"));
        return false;
    }
    if (static_cast<qint64>(request.logicalX) + request.logicalWidth - 1
          > std::numeric_limits<qint32>::max()
        || static_cast<qint64>(request.logicalY) + request.logicalHeight - 1
          > std::numeric_limits<qint32>::max()) {
        fail(QStringLiteral("request logical rectangle overflows coordinates"));
        return false;
    }

    const quint64 pixelCount = static_cast<quint64>(request.pixelWidth)
      * static_cast<quint64>(request.pixelHeight);
    if (pixelCount > MaxPixelCount) {
        fail(QStringLiteral("request pixel count exceeds protocol bounds"));
        return false;
    }
    if (!validScale(request.scaleNumerator, request.scaleDenominator)) {
        fail(QStringLiteral("request scale is outside protocol bounds"));
        return false;
    }
    if (!mappingMatches(request.logicalX,
                        request.logicalWidth,
                        request.pixelWidth,
                        request.scaleNumerator,
                        request.scaleDenominator)
        || !mappingMatches(request.logicalY,
                           request.logicalHeight,
                           request.pixelHeight,
                           request.scaleNumerator,
                           request.scaleDenominator)) {
        fail(QStringLiteral(
          "request pixel dimensions contradict its logical rectangle and scale"));
        return false;
    }
    if (request.payloadLength < static_cast<quint64>(PngPrefixSize)
        || request.payloadLength > MaxPngBytes) {
        fail(QStringLiteral("request PNG length is outside protocol bounds"));
        return false;
    }

    m_request = std::move(request);
    m_headerParsed = true;
    return true;
}

bool RequestParser::validatePngPrefix()
{
    const qsizetype size = m_request.png.size();
    if (size >= 8
        && m_request.png.left(8)
          != QByteArray(reinterpret_cast<const char*>(PngSignature), 8)) {
        fail(QStringLiteral("request payload has an invalid PNG signature"));
        return false;
    }
    if (size >= 16
        && (readU32(m_request.png, 8) != 13
            || m_request.png.mid(12, 4) != QByteArrayLiteral("IHDR"))) {
        fail(QStringLiteral("request payload has an invalid PNG IHDR"));
        return false;
    }
    if (size >= PngPrefixSize) {
        if (readU32(m_request.png, 16) != m_request.pixelWidth
            || readU32(m_request.png, 20) != m_request.pixelHeight) {
            fail(QStringLiteral(
              "request PNG dimensions do not match the request header"));
            return false;
        }
        m_pngPrefixValidated = true;
    }
    return true;
}

bool RequestParser::validatePngStructure()
{
    const QByteArray& png = m_request.png;
    const unsigned char bitDepth =
      static_cast<unsigned char>(png.at(24));
    const unsigned char colorType =
      static_cast<unsigned char>(png.at(25));
    if (bitDepth != 8
        || (colorType != 0 && colorType != 2 && colorType != 4
            && colorType != 6)
        || png.at(26) != 0 || png.at(27) != 0 || png.at(28) != 0) {
        fail(QStringLiteral(
          "PNG must be a non-interlaced 8-bit static image"));
        return false;
    }
    quint64 offset = 8;
    quint64 ancillaryBytes = 0;
    quint32 chunks = 0;
    bool sawIhdr = false;
    bool sawPlte = false;
    bool sawIdat = false;
    bool endedIdat = false;
    bool sawIend = false;

    while (offset < static_cast<quint64>(png.size())) {
        if (++chunks > MaxPngChunks
            || static_cast<quint64>(png.size()) - offset < 12) {
            fail(QStringLiteral("PNG chunk framing is invalid"));
            return false;
        }
        const quint32 dataLength =
          readU32(png, static_cast<qsizetype>(offset));
        const quint64 chunkLength = static_cast<quint64>(dataLength) + 12;
        if (chunkLength > static_cast<quint64>(png.size()) - offset) {
            fail(QStringLiteral("PNG chunk exceeds the payload"));
            return false;
        }
        const QByteArray type =
          png.mid(static_cast<qsizetype>(offset + 4), 4);
        if (type.size() != 4) {
            fail(QStringLiteral("PNG chunk type is truncated"));
            return false;
        }

        if (!sawIhdr) {
            if (type != QByteArrayLiteral("IHDR") || dataLength != 13) {
                fail(QStringLiteral("PNG IHDR must be the first chunk"));
                return false;
            }
            sawIhdr = true;
        } else if (type == QByteArrayLiteral("IHDR")) {
            fail(QStringLiteral("PNG contains multiple IHDR chunks"));
            return false;
        }

        if (type == QByteArrayLiteral("acTL")
            || type == QByteArrayLiteral("fcTL")
            || type == QByteArrayLiteral("fdAT")) {
            fail(QStringLiteral("animated PNG payloads are not accepted"));
            return false;
        }

        const bool critical =
          (static_cast<unsigned char>(type.at(0)) & 0x20U) == 0;
        if (critical && type != QByteArrayLiteral("IHDR")
            && type != QByteArrayLiteral("PLTE")
            && type != QByteArrayLiteral("IDAT")
            && type != QByteArrayLiteral("IEND")) {
            fail(QStringLiteral("PNG contains an unknown critical chunk"));
            return false;
        }
        if (!critical) {
            ancillaryBytes += dataLength;
            if (ancillaryBytes > MaxAncillaryBytes) {
                fail(QStringLiteral("PNG ancillary metadata exceeds protocol bounds"));
                return false;
            }
        }

        if (type == QByteArrayLiteral("PLTE")) {
            if (sawPlte || sawIdat || dataLength == 0 || dataLength % 3 != 0) {
                fail(QStringLiteral("PNG palette chunk is invalid"));
                return false;
            }
            sawPlte = true;
        } else if (type == QByteArrayLiteral("IDAT")) {
            if (endedIdat || dataLength == 0) {
                fail(QStringLiteral("PNG image-data chunks are invalid"));
                return false;
            }
            sawIdat = true;
        } else if (sawIdat && type != QByteArrayLiteral("IEND")) {
            endedIdat = true;
        }

        if (type == QByteArrayLiteral("IEND")) {
            if (!sawIdat || dataLength != 0
                || offset + chunkLength != static_cast<quint64>(png.size())) {
                fail(QStringLiteral("PNG IEND chunk is invalid"));
                return false;
            }
            sawIend = true;
        }
        offset += chunkLength;
    }

    if (!sawIhdr || !sawIdat || !sawIend) {
        fail(QStringLiteral("PNG payload is incomplete"));
        return false;
    }
    return true;
}

bool isKnownAckStatus(quint32 status)
{
    switch (static_cast<AckStatus>(status)) {
        case AckStatus::RequestAccepted:
        case AckStatus::EditorReady:
        case AckStatus::CommitAccepted:
        case AckStatus::MalformedRequest:
        case AckStatus::Busy:
        case AckStatus::DecodeFailed:
        case AckStatus::InternalError:
        case AckStatus::Timeout:
            return true;
    }
    return false;
}

QByteArray encodeAck(const Ack& ack)
{
    QByteArray bytes(AckSize, '\0');
    std::copy_n(AckMagic, 8, bytes.data());
    writeU64(bytes, 8, ack.captureId);
    writeU32(bytes, 16, static_cast<quint32>(ack.status));
    writeU32(bytes, 20, ack.daemonPid);
    return bytes;
}

bool decodeAck(const QByteArray& bytes, Ack* ack, QString* errorString)
{
    auto failDecode = [errorString](const QString& message) {
        if (errorString != nullptr) {
            *errorString = message;
        }
        return false;
    };

    if (bytes.size() != AckSize) {
        return failDecode(QStringLiteral("ACK must be exactly 32 bytes"));
    }
    if (bytes.left(8) != QByteArray(AckMagic, 8)) {
        return failDecode(QStringLiteral("ACK magic is invalid"));
    }
    const quint32 rawStatus = readU32(bytes, 16);
    if (!isKnownAckStatus(rawStatus)) {
        return failDecode(QStringLiteral("ACK status is invalid"));
    }
    if (readU64(bytes, 24) != 0) {
        return failDecode(QStringLiteral("ACK reserved field must be zero"));
    }

    if (ack != nullptr) {
        ack->captureId = readU64(bytes, 8);
        ack->status = static_cast<AckStatus>(rawStatus);
        ack->daemonPid = readU32(bytes, 20);
    }
    if (errorString != nullptr) {
        errorString->clear();
    }
    return true;
}

QByteArray encodeCommit(quint64 captureId)
{
    QByteArray bytes(CommitSize, '\0');
    std::copy_n(CommitMagic, 8, bytes.data());
    writeU64(bytes, 8, captureId);
    return bytes;
}

bool decodeCommit(const QByteArray& bytes,
                  quint64* captureId,
                  QString* errorString)
{
    auto failDecode = [errorString](const QString& message) {
        if (errorString != nullptr) {
            *errorString = message;
        }
        return false;
    };
    if (bytes.size() != CommitSize) {
        return failDecode(QStringLiteral("commit must be exactly 16 bytes"));
    }
    if (bytes.left(8) != QByteArray(CommitMagic, 8)) {
        return failDecode(QStringLiteral("commit magic is invalid"));
    }
    const quint64 decodedId = readU64(bytes, 8);
    if (decodedId == 0) {
        return failDecode(QStringLiteral("commit capture ID must be nonzero"));
    }
    if (captureId != nullptr) {
        *captureId = decodedId;
    }
    if (errorString != nullptr) {
        errorString->clear();
    }
    return true;
}

} // namespace ShellBridgeProtocol
