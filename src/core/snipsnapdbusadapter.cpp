// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2017-2019 Alejandro Sirgo Rica & Contributors

#include "snipsnapdbusadapter.h"
#include "core/snipsnap.h"
#include "core/snipsnapdaemon.h"

SnipSnapDBusAdapter::SnipSnapDBusAdapter(QObject* parent)
  : QDBusAbstractAdaptor(parent)
{}

SnipSnapDBusAdapter::~SnipSnapDBusAdapter() = default;

void SnipSnapDBusAdapter::captureScreen()
{
    SnipSnap::instance()->gui(CaptureRequest(CaptureRequest::GRAPHICAL_MODE));
}

void SnipSnapDBusAdapter::attachScreenshotToClipboard(const QByteArray& data)
{
    SnipSnapDaemon::instance()->attachScreenshotToClipboard(data);
}

void SnipSnapDBusAdapter::attachTextToClipboard(const QString& text,
                                                 const QString& notification)
{
    SnipSnapDaemon::instance()->attachTextToClipboard(text, notification);
}

void SnipSnapDBusAdapter::attachPin(const QByteArray& data)
{
    SnipSnapDaemon::instance()->attachPin(data);
}
