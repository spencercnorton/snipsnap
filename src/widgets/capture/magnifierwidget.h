#pragma once

#include "tools/capturesourcemapping.h"

#include <QWidget>

#include <cmath>

class QPropertyAnimation;

class MagnifierWidget : public QWidget
{
    Q_OBJECT
public:
    explicit MagnifierWidget(const QPixmap& p,
                             const QColor& c,
                             bool isSquare,
                             QWidget* parent = nullptr,
                             const CaptureSourceMapping& sourceMapping = {});

    static QRect sourceRectForEditorPoint(
      const CaptureSourceMapping& sourceMapping,
      const QPointF& editorPoint,
      int radius,
      bool paddedSource)
    {
        QPointF center = sourceMapping.isValid()
          ? sourceMapping.sourcePixelF(editorPoint)
          : editorPoint;
        if (paddedSource) {
            center += QPointF(radius, radius);
        }
        return { static_cast<int>(std::floor(center.x() - radius)),
                 static_cast<int>(std::floor(center.y() - radius)),
                 radius * 2 + 1,
                 radius * 2 + 1 };
    }

protected:
    void paintEvent(QPaintEvent*) override;

private:
    const int m_magPixels = 8;
    const int m_magOffset = 16;
    const int magZoom = 10;
    const int m_pixels = 2 * m_magPixels + 1;
    bool m_square;
    QColor m_color;
    QColor m_borderColor;
    QPixmap m_screenshot;
    QPixmap m_paddedScreenshot;
    CaptureSourceMapping m_sourceMapping;
    void drawMagnifier(QPainter& painter);
    void drawMagnifierCircle(QPainter& painter);
};
