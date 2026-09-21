// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#include "core/shellbridgeprotocol.h"

#include <QTest>
#include <QtEndian>

namespace
{

const QByteArray GoldenPng = QByteArray::fromHex(
  "89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c02"
  "0000000b4944415478da6364f80f00010501012718e3660000000049454e44ae4260"
  "82");

const QByteArray GoldenRequest = QByteArray::fromHex(
  "46534252504e473100000040000000000102030405060708fffff880ffffff880000"
  "000100000001000000010000000100000001000000010000000000000044"
  "89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c02"
  "0000000b4944415478da6364f80f00010501012718e3660000000049454e44ae4260"
  "82");

const QByteArray GoldenAcceptedAck = QByteArray::fromHex(
  "4653425241434b31010203040506070800000001000010920000000000000000");

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

} // namespace

class ShellBridgeProtocolTest : public QObject
{
    Q_OBJECT

private slots:
    void parsesGoldenRequestOneByteAtATime()
    {
        ShellBridgeProtocol::RequestParser parser;
        for (qsizetype index = 0; index < GoldenRequest.size(); ++index) {
            const auto expected = index + 1 == GoldenRequest.size()
              ? ShellBridgeProtocol::ParseStatus::Complete
              : ShellBridgeProtocol::ParseStatus::Incomplete;
            QCOMPARE(parser.append(GoldenRequest.mid(index, 1)), expected);
        }

        const auto& request = parser.request();
        QCOMPARE(request.captureId, UINT64_C(0x0102030405060708));
        QCOMPARE(request.logicalX, -1920);
        QCOMPARE(request.logicalY, -120);
        QCOMPARE(request.logicalWidth, 1U);
        QCOMPARE(request.logicalHeight, 1U);
        QCOMPARE(request.pixelWidth, 1U);
        QCOMPARE(request.pixelHeight, 1U);
        QCOMPARE(request.scaleNumerator, 1U);
        QCOMPARE(request.scaleDenominator, 1U);
        QCOMPARE(request.payloadLength, static_cast<quint64>(GoldenPng.size()));
        QCOMPARE(request.png, GoldenPng);
        QVERIFY(parser.errorString().isEmpty());
    }

    void rejectsInvalidHeaderFields_data()
    {
        QTest::addColumn<int>("offset");
        QTest::addColumn<quint64>("value");
        QTest::addColumn<bool>("wide");

        QTest::newRow("header-size")
          << 8 << static_cast<quint64>(63) << false;
        QTest::newRow("flags")
          << 12 << static_cast<quint64>(1) << false;
        QTest::newRow("zero-capture-id")
          << 16 << static_cast<quint64>(0) << true;
        QTest::newRow("zero-logical-width")
          << 32 << static_cast<quint64>(0) << false;
        QTest::newRow("oversize-logical-height")
          << 36 << static_cast<quint64>(32769) << false;
        QTest::newRow("zero-pixel-width")
          << 40 << static_cast<quint64>(0) << false;
        QTest::newRow("zero-scale-denominator")
          << 52 << static_cast<quint64>(0) << false;
        QTest::newRow("scale-below-half")
          << 48 << static_cast<quint64>(1) << false;
        QTest::newRow("scale-above-four")
          << 48 << static_cast<quint64>(5) << false;
        QTest::newRow("short-png")
          << 56 << static_cast<quint64>(32) << true;
        QTest::newRow("oversize-png")
          << 56 << ShellBridgeProtocol::MaxPngBytes + 1 << true;
    }

    void rejectsInvalidHeaderFields()
    {
        QFETCH(int, offset);
        QFETCH(quint64, value);
        QFETCH(bool, wide);
        QByteArray request = GoldenRequest;
        if (qstrcmp(QTest::currentDataTag(), "scale-below-half") == 0) {
            writeU32(request, 52, 3);
        }
        if (wide) {
            writeU64(request, offset, value);
        } else {
            writeU32(request, offset, static_cast<quint32>(value));
        }

        ShellBridgeProtocol::RequestParser parser;
        QCOMPARE(parser.append(request.left(ShellBridgeProtocol::RequestHeaderSize)),
                 ShellBridgeProtocol::ParseStatus::Error);
        QVERIFY(!parser.errorString().isEmpty());
    }

    void rejectsExcessivePixelCount()
    {
        QByteArray request = GoldenRequest;
        writeU32(request, 40, 8'000);
        writeU32(request, 44, 4'001);

        ShellBridgeProtocol::RequestParser parser;
        QCOMPARE(parser.append(request.left(ShellBridgeProtocol::RequestHeaderSize)),
                 ShellBridgeProtocol::ParseStatus::Error);
        QVERIFY(parser.errorString().contains(QStringLiteral("pixel count")));
    }

    void rejectsOverflowingOrContradictoryGeometry()
    {
        QByteArray overflow = GoldenRequest;
        writeU32(overflow, 24, 0x7fffffffU);
        writeU32(overflow, 32, 2);
        ShellBridgeProtocol::RequestParser overflowParser;
        QCOMPARE(overflowParser.append(
                   overflow.left(ShellBridgeProtocol::RequestHeaderSize)),
                 ShellBridgeProtocol::ParseStatus::Error);
        QVERIFY(overflowParser.errorString().contains(
          QStringLiteral("overflows")));

        QByteArray contradictory = GoldenRequest;
        writeU32(contradictory, 32, 100);
        writeU32(contradictory, 40, 10);
        ShellBridgeProtocol::RequestParser mappingParser;
        QCOMPARE(mappingParser.append(
                   contradictory.left(ShellBridgeProtocol::RequestHeaderSize)),
                 ShellBridgeProtocol::ParseStatus::Error);
        QVERIFY(mappingParser.errorString().contains(
          QStringLiteral("contradict")));
    }

    void rejectsInvalidPngPrefixAndDimensions()
    {
        QByteArray invalidSignature = GoldenRequest;
        invalidSignature[ShellBridgeProtocol::RequestHeaderSize] = 'X';
        ShellBridgeProtocol::RequestParser signatureParser;
        QCOMPARE(signatureParser.append(invalidSignature),
                 ShellBridgeProtocol::ParseStatus::Error);

        QByteArray invalidIhdr = GoldenRequest;
        invalidIhdr[ShellBridgeProtocol::RequestHeaderSize + 12] = 'X';
        ShellBridgeProtocol::RequestParser ihdrParser;
        QCOMPARE(ihdrParser.append(invalidIhdr),
                 ShellBridgeProtocol::ParseStatus::Error);

        QByteArray mismatchedDimensions = GoldenRequest;
        writeU32(mismatchedDimensions,
                 ShellBridgeProtocol::RequestHeaderSize + 16,
                 2);
        ShellBridgeProtocol::RequestParser dimensionParser;
        QCOMPARE(dimensionParser.append(mismatchedDimensions),
                 ShellBridgeProtocol::ParseStatus::Error);
        QVERIFY(dimensionParser.errorString().contains(
          QStringLiteral("dimensions")));
    }

    void rejectsUnboundedOrNonStaticPngStructure()
    {
        QByteArray missingIend = GoldenRequest.left(GoldenRequest.size() - 12);
        writeU64(missingIend,
                 56,
                 static_cast<quint64>(GoldenPng.size() - 12));
        ShellBridgeProtocol::RequestParser incompleteParser;
        QCOMPARE(incompleteParser.append(missingIend),
                 ShellBridgeProtocol::ParseStatus::Error);

        QByteArray animated = GoldenRequest;
        const qsizetype iendOffset = animated.size() - 12;
        const QByteArray animationChunk = QByteArray::fromHex(
          "000000086163544c000000010000000000000000");
        animated.insert(iendOffset, animationChunk);
        writeU64(animated,
                 56,
                 static_cast<quint64>(animated.size()
                                      - ShellBridgeProtocol::RequestHeaderSize));
        ShellBridgeProtocol::RequestParser animatedParser;
        QCOMPARE(animatedParser.append(animated),
                 ShellBridgeProtocol::ParseStatus::Error);
        QVERIFY(animatedParser.errorString().contains(
          QStringLiteral("animated")));

        QByteArray metadata(4 + 4 + 4097 + 4, '\0');
        writeU32(metadata, 0, 4097);
        metadata.replace(4, 4, QByteArrayLiteral("tEXt"));
        QByteArray excessiveMetadata = GoldenRequest;
        excessiveMetadata.insert(excessiveMetadata.size() - 12, metadata);
        writeU64(excessiveMetadata,
                 56,
                 static_cast<quint64>(
                   excessiveMetadata.size()
                   - ShellBridgeProtocol::RequestHeaderSize));
        ShellBridgeProtocol::RequestParser metadataParser;
        QCOMPARE(metadataParser.append(excessiveMetadata),
                 ShellBridgeProtocol::ParseStatus::Error);
        QVERIFY(metadataParser.errorString().contains(
          QStringLiteral("metadata")));
    }

    void rejectsTrailingBytesInSameOrLaterChunk()
    {
        ShellBridgeProtocol::RequestParser sameChunkParser;
        QCOMPARE(sameChunkParser.append(GoldenRequest + QByteArrayLiteral("x")),
                 ShellBridgeProtocol::ParseStatus::Error);

        ShellBridgeProtocol::RequestParser laterChunkParser;
        QCOMPARE(laterChunkParser.append(GoldenRequest),
                 ShellBridgeProtocol::ParseStatus::Complete);
        QCOMPARE(laterChunkParser.append(QByteArrayLiteral("x")),
                 ShellBridgeProtocol::ParseStatus::Error);
    }

    void resetAllowsParserReuse()
    {
        ShellBridgeProtocol::RequestParser parser;
        QCOMPARE(parser.append(QByteArrayLiteral("bad request")),
                 ShellBridgeProtocol::ParseStatus::Incomplete);
        parser.reset();
        QCOMPARE(parser.append(GoldenRequest),
                 ShellBridgeProtocol::ParseStatus::Complete);
    }

    void ackMatchesGoldenVectorAndRoundTrips()
    {
        const ShellBridgeProtocol::Ack expected{
            UINT64_C(0x0102030405060708),
            ShellBridgeProtocol::AckStatus::RequestAccepted,
            4242,
        };
        const QByteArray encoded = ShellBridgeProtocol::encodeAck(expected);
        QCOMPARE(encoded, GoldenAcceptedAck);

        ShellBridgeProtocol::Ack decoded;
        QString error;
        QVERIFY(ShellBridgeProtocol::decodeAck(encoded, &decoded, &error));
        QVERIFY(error.isEmpty());
        QCOMPARE(decoded.captureId, expected.captureId);
        QCOMPARE(decoded.status, expected.status);
        QCOMPARE(decoded.daemonPid, expected.daemonPid);
    }

    void ackRejectsReservedUnknownAndTrailingBytes()
    {
        QByteArray reserved = GoldenAcceptedAck;
        writeU64(reserved, 24, 1);
        QVERIFY(!ShellBridgeProtocol::decodeAck(reserved, nullptr));

        QByteArray unknown = GoldenAcceptedAck;
        writeU32(unknown, 16, 99);
        QVERIFY(!ShellBridgeProtocol::decodeAck(unknown, nullptr));

        QVERIFY(!ShellBridgeProtocol::decodeAck(
          GoldenAcceptedAck + QByteArrayLiteral("x"), nullptr));
    }

    void commitMatchesGoldenVectorAndRejectsMalformedFrames()
    {
        const quint64 expected = UINT64_C(0x0102030405060708);
        const QByteArray golden = QByteArray::fromHex(
          "46534252434d54310102030405060708");
        QCOMPARE(ShellBridgeProtocol::encodeCommit(expected), golden);

        quint64 decoded = 0;
        QString error;
        QVERIFY(ShellBridgeProtocol::decodeCommit(golden, &decoded, &error));
        QCOMPARE(decoded, expected);
        QVERIFY(error.isEmpty());

        QByteArray badMagic = golden;
        badMagic[0] = 'X';
        QVERIFY(!ShellBridgeProtocol::decodeCommit(badMagic, nullptr));
        QVERIFY(!ShellBridgeProtocol::decodeCommit(golden + 'x', nullptr));
        QVERIFY(!ShellBridgeProtocol::decodeCommit(
          ShellBridgeProtocol::encodeCommit(0), nullptr));
    }
};

QTEST_GUILESS_MAIN(ShellBridgeProtocolTest)

#include "shell_bridge_protocol_test.moc"
