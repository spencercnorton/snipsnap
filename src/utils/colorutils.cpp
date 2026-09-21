// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2017-2019 Alejandro Sirgo Rica & Contributors

#include "colorutils.h"

#include <cmath>

namespace {

qreal linearizeSrgb(qreal channel)
{
    return channel <= 0.04045 ? channel / 12.92
                              : std::pow((channel + 0.055) / 1.055, 2.4);
}

qreal relativeLuminance(const QColor& color)
{
    return 0.2126 * linearizeSrgb(color.redF()) +
           0.7152 * linearizeSrgb(color.greenF()) +
           0.0722 * linearizeSrgb(color.blueF());
}

} // namespace

bool ColorUtils::colorIsDark(const QColor& c)
{
    // WCAG contrast ratios select white over black below this luminance:
    // 1.05 / (L + 0.05) > (L + 0.05) / 0.05.
    constexpr qreal blackWhiteContrastCrossover = 0.1791287847;
    return relativeLuminance(c) < blackWhiteContrastCrossover;
}

QColor ColorUtils::contrastColor(const QColor& c)
{
    int change = colorIsDark(c) ? 30 : -45;

    return { qBound(0, c.red() + change, 255),
             qBound(0, c.green() + change, 255),
             qBound(0, c.blue() + change, 255) };
}
