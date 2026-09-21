// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2021 Jeremy Borgman

#include "cacheutils.h"
#include <QDataStream>
#include <QDebug>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QRect>
#include <QSaveFile>
#include <QStandardPaths>
#include <QString>

bool ensureOwnerOnlyDirectory(const QString& path)
{
    QDir directory(path);
    if (!directory.exists() && !QDir().mkpath(path)) {
        return false;
    }
#if defined(Q_OS_UNIX)
    return QFile::setPermissions(
      path,
      QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
#else
    return true;
#endif
}

bool ensureOwnerOnlyFile(const QString& path)
{
    if (!QFileInfo::exists(path)) {
        return false;
    }
#if defined(Q_OS_UNIX)
    return QFile::setPermissions(
      path, QFileDevice::ReadOwner | QFileDevice::WriteOwner);
#else
    return true;
#endif
}

QString getCachePath()
{
    auto cachePath =
      QStandardPaths::writableLocation(QStandardPaths::CacheLocation);
    if (!ensureOwnerOnlyDirectory(cachePath)) {
        qWarning() << "Unable to secure the SnipSnap cache directory:"
                   << cachePath;
        return {};
    }
    return cachePath;
}

void setLastRegion(QRect const& newRegion)
{
    const QString cacheDirectory = getCachePath();
    if (cacheDirectory.isEmpty()) {
        qWarning() << "Refusing to write an insecure capture region cache";
        return;
    }
    const QString cachePath = cacheDirectory + "/region.txt";

    QSaveFile file(cachePath);
    if (file.open(QIODevice::WriteOnly)) {
        if (!file.setPermissions(QFileDevice::ReadOwner |
                                 QFileDevice::WriteOwner)) {
            file.cancelWriting();
            qWarning() << "Unable to secure the last capture region cache";
            return;
        }
        QDataStream out(&file);
        out << newRegion;
        if (out.status() != QDataStream::Ok) {
            file.cancelWriting();
            qWarning() << "Unable to serialize the last capture region";
            return;
        }
        if (!file.commit()) {
            qWarning() << "Unable to atomically save the last capture region";
            return;
        }
        if (!ensureOwnerOnlyFile(cachePath)) {
            qWarning() << "Unable to secure the saved capture region cache";
        }
    } else {
        qWarning()
          << "Unable to open the last capture region cache for writing";
    }
}

QRect getLastRegion()
{
    const QString cacheDirectory = getCachePath();
    if (cacheDirectory.isEmpty()) {
        return {};
    }
    const QString cachePath = cacheDirectory + "/region.txt";
    if (QFileInfo::exists(cachePath) && !ensureOwnerOnlyFile(cachePath)) {
        qWarning() << "Unable to secure the last capture region cache";
    }
    QFile file(cachePath);

    QRect lastRegion;
    if (file.open(QIODevice::ReadOnly)) {
        QDataStream input(&file);
        input >> lastRegion;
        file.close();
    } else {
        lastRegion = QRect(0, 0, 0, 0);
    }

    return lastRegion;
}
