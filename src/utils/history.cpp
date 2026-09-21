#include "history.h"
#include "config/cacheutils.h"
#include "utils/confighandler.h"

#include <QDebug>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QSaveFile>
#include <QStringList>
#if defined(Q_OS_MACOS)
#include <QProcessEnvironment>
#endif

History::History()
{
#ifdef Q_OS_WIN
    // Preserve the established Windows location for compatibility.
    m_historyPath =
      QDir::homePath() + QStringLiteral("/AppData/Roaming/snipsnap/history/");
#elif defined(Q_OS_MACOS)
    // Preserve the pre-v14 location so existing upload/delete history remains
    // visible after an upgrade.
    const QString cachePath = QProcessEnvironment::systemEnvironment().value(
      QStringLiteral("XDG_CACHE_HOME"),
      QDir::homePath() + QStringLiteral("/.cache"));
    m_historyPath = cachePath + QStringLiteral("/snipsnap/history/");
#else
    const QString cachePath = getCachePath();
    if (cachePath.isEmpty()) {
        return;
    }
    m_historyPath = cachePath + QStringLiteral("/history/");
#endif
    if (!ensureOwnerOnlyDirectory(m_historyPath)) {
        qWarning() << "Unable to secure the screenshot history directory:"
                   << m_historyPath;
        m_historyPath.clear();
        return;
    }

    const QDir directory(m_historyPath);
    const QFileInfoList existingFiles =
      directory.entryInfoList(QDir::Files | QDir::NoDotAndDotDot);
    for (const QFileInfo& file : existingFiles) {
        if (!ensureOwnerOnlyFile(file.absoluteFilePath())) {
            qWarning() << "Unable to secure screenshot history file:"
                       << file.absoluteFilePath();
        }
    }
}

const QString& History::path()
{
    return m_historyPath;
}

void History::save(const QPixmap& pixmap, const QString& fileName)
{
    if (m_historyPath.isEmpty()) {
        qWarning() << "Refusing to write an insecure screenshot history";
        return;
    }
    const QString safeFileName = QFileInfo(fileName).fileName();
    if (safeFileName.isEmpty() || safeFileName == QStringLiteral(".") ||
        safeFileName == QStringLiteral("..") || safeFileName != fileName) {
        qWarning() << "Refusing unsafe screenshot history filename";
        return;
    }

    // scale preview only in local disk
    QPixmap pixmapScaled = QPixmap(pixmap);
    if (pixmap.height() / HISTORYPIXMAP_MAX_PREVIEW_HEIGHT >=
        pixmap.width() / HISTORYPIXMAP_MAX_PREVIEW_WIDTH) {
        pixmapScaled = pixmap.scaledToHeight(HISTORYPIXMAP_MAX_PREVIEW_HEIGHT,
                                             Qt::SmoothTransformation);
    } else {
        pixmapScaled = pixmap.scaledToWidth(HISTORYPIXMAP_MAX_PREVIEW_WIDTH,
                                            Qt::SmoothTransformation);
    }

    // save preview
    const QString destination = path() + safeFileName;
    QSaveFile file(destination);
    if (file.open(QIODevice::WriteOnly)) {
        if (!file.setPermissions(QFileDevice::ReadOwner |
                                 QFileDevice::WriteOwner)) {
            file.cancelWriting();
            qWarning() << "Unable to secure screenshot history preview";
            return;
        }
        if (!pixmapScaled.save(&file, "PNG")) {
            file.cancelWriting();
            qWarning() << "Unable to encode screenshot history preview";
            return;
        }
        if (!file.commit()) {
            qWarning()
              << "Unable to atomically save screenshot history preview";
            return;
        }
        if (!ensureOwnerOnlyFile(destination)) {
            qWarning()
              << "Unable to secure the saved screenshot history preview";
        }
    } else {
        qWarning() << "Unable to open screenshot history preview for writing";
    }

    history();
}

const QList<QString>& History::history()
{
    m_thumbs.clear();
    if (m_historyPath.isEmpty()) {
        return m_thumbs;
    }
    QDir directory(path());
    QStringList images = directory.entryList(QStringList() << "*.png"
                                                           << "*.PNG",
                                             QDir::Files,
                                             QDir::Time);
    int cnt = 0;
    int max = ConfigHandler().uploadHistoryMax();
    for (const auto& fileName : images) {
        if (++cnt <= max) {
            m_thumbs.append(fileName);
        } else {
            QFile file(path() + fileName);
            file.remove();
        }
    }
    return m_thumbs;
}

const HistoryFileName& History::unpackFileName(const QString& fileNamePacked)
{
    int nPathIndex = fileNamePacked.lastIndexOf("/");
    QStringList unpackedFileName;
    if (nPathIndex == -1) {
        unpackedFileName = fileNamePacked.split("-");
    } else {
        unpackedFileName = fileNamePacked.mid(nPathIndex + 1).split("-");
    }

    switch (unpackedFileName.length()) {
        case 3:
            m_unpackedFileName.file = unpackedFileName[2];
            m_unpackedFileName.token = unpackedFileName[1];
            m_unpackedFileName.type = unpackedFileName[0];
            break;
        case 2:
            m_unpackedFileName.file = unpackedFileName[1];
            m_unpackedFileName.token = "";
            m_unpackedFileName.type = unpackedFileName[0];
            break;
        default:
            m_unpackedFileName.file = unpackedFileName[0];
            m_unpackedFileName.token = "";
            m_unpackedFileName.type = "";
            break;
    }
    return m_unpackedFileName;
}

const QString& History::packFileName(const QString& storageType,
                                     const QString& deleteToken,
                                     const QString& fileName)
{
    m_packedFileName = fileName;
    if (storageType.length() > 0) {
        if (deleteToken.length() > 0) {
            m_packedFileName =
              storageType + "-" + deleteToken + "-" + m_packedFileName;
        } else {
            m_packedFileName = storageType + "-" + m_packedFileName;
        }
    }
    return m_packedFileName;
}
