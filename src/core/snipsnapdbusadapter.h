// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2017-2019 Alejandro Sirgo Rica & Contributors

#pragma once

#include <QtDBus/QDBusAbstractAdaptor>

class SnipSnapDBusAdapter : public QDBusAbstractAdaptor
{
    Q_OBJECT
    Q_CLASSINFO("D-Bus Interface", "tech.norvi.snipsnap")

public:
    explicit SnipSnapDBusAdapter(QObject* parent = nullptr);
    virtual ~SnipSnapDBusAdapter();

public slots:
    Q_NOREPLY void captureScreen();
    Q_NOREPLY void attachScreenshotToClipboard(const QByteArray& data);
    Q_NOREPLY void attachTextToClipboard(const QString& text,
                                         const QString& notification);
    Q_NOREPLY void attachPin(const QByteArray& data);
};
