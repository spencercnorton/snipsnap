// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2021 Jeremy Borgman

#pragma once

class QString;
class QRect;

QString getCachePath();
bool ensureOwnerOnlyDirectory(const QString& path);
bool ensureOwnerOnlyFile(const QString& path);
QRect getLastRegion();
void setLastRegion(QRect const& newRegion);
