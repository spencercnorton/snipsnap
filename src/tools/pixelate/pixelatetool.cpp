// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2017-2019 Alejandro Sirgo Rica & Contributors

#include "pixelatetool.h"
#include "pixelaterenderer.h"
#include "utils/confighandler.h"

#include <QApplication>
#include <QPainter>

PixelateTool::PixelateTool(QObject* parent)
  : AbstractTwoPointTool(parent)
{}

QIcon PixelateTool::icon(const QColor& background, bool inEditor) const
{
    Q_UNUSED(inEditor)
    return QIcon(iconPath(background) + "pixelate.svg");
}

QString PixelateTool::name() const
{
    return tr("Pixelate");
}

CaptureTool::Type PixelateTool::type() const
{
    return CaptureTool::TYPE_PIXELATE;
}

QString PixelateTool::description() const
{
    return tr("Set Pixelate as the paint tool.");
}

QRect PixelateTool::boundingRect() const
{
    return QRect(points().first, points().second).normalized();
}

CaptureTool* PixelateTool::copy(QObject* parent)
{
    auto* tool = new PixelateTool(parent);
    copyParams(this, tool);
    return tool;
}

/**
 * Since pixelation does not protect the contents of the pixelated area
 * (see e.g. https://github.com/bishopfox/unredacter),
 * _pseudo-pixelation_ is used:
 *
 * Only colors from the fringe of the selected area are used to generate
 * a pixelation-like effect. The interior of the selected area is not used
 * as an input at all and hence can not be recovered.
 *
 */
void PixelateTool::process(QPainter& painter, const QPixmap& pixmap)
{
    bool useInsecurePixelate = ConfigHandler().insecurePixelate();

    const QRect selection = sourceLogicalRect(boundingRect(), pixmap);
    const QRect selectionScaled = sourcePixelRect(selection, pixmap);
    const QRectF destination = sourceDestinationRect(selection, selectionScaled);
    if (selection.isEmpty() || selectionScaled.isEmpty()
        || destination.isEmpty()) {
        return;
    }
    PixelateRenderer::render(painter,
                             pixmap,
                             selection,
                             selectionScaled,
                             destination,
                             size(),
                             useInsecurePixelate,
                             this);
}

void PixelateTool::drawSearchArea(QPainter& painter, const QPixmap& pixmap)
{
    Q_UNUSED(pixmap)
    painter.fillRect(boundingRect(), QBrush(Qt::black));
}

void PixelateTool::paintMousePreview(QPainter& painter,
                                     const CaptureContext& context)
{
    Q_UNUSED(context)
    Q_UNUSED(painter)
}

void PixelateTool::pressed(CaptureContext& context)
{
    Q_UNUSED(context)
}
