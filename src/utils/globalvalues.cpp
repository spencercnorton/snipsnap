// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2017-2019 Alejandro Sirgo Rica & Contributors

#include "globalvalues.h"

#include <QApplication>
#include <QFontMetrics>
#if defined(Q_OS_MACOS)
#include <QOperatingSystemVersion>
#endif

int GlobalValues::buttonBaseSize()
{
    return QFontMetrics(qApp->font()).lineSpacing() * 2.2;
}

QString GlobalValues::versionInfo()
{
    return QStringLiteral("SnipSnap " APP_VERSION " (" SNIPSNAP_GIT_HASH ")"
                          "\nCompiled with Qt " QT_VERSION_STR);
}

QString GlobalValues::iconPath()
{
#if USE_MONOCHROME_ICON
    return QString(":img/app/snipsnap.monochrome.svg");
#else
    return { ":img/app/snipsnap.svg" };
#endif
}

QString GlobalValues::iconPathPNG()
{
#if USE_MONOCHROME_ICON
    return QString(":img/app/snipsnap.monochrome.png");
#else
    return { ":img/app/snipsnap.png" };
#endif
}

QString GlobalValues::trayIconPath()
{
#if defined(Q_OS_MACOS)
    auto currentMacOsVersion = QOperatingSystemVersion::current();
    if (currentMacOsVersion >= QOperatingSystemVersion::MacOSBigSur) {
        return { ":img/app/snipsnap.mask.png" };
    }
#endif
    return { ":img/app/snipsnap.monochrome.png" };
}
