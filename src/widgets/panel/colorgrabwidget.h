#pragma once

#include "tools/capturesourcemapping.h"

#include <QPointer>
#include <QWidget>

class SidePanelWidget;
class OverlayMessage;

class ColorGrabWidget : public QWidget
{
    Q_OBJECT
public:
    explicit ColorGrabWidget(QPixmap* p, QWidget* parent = nullptr);
    ColorGrabWidget(QPixmap* p,
                    const CaptureSourceMapping& sourceMapping,
                    QWidget* captureSurface,
                    QWidget* parent = nullptr);

    void startGrabbing();

    QColor color();
    QPoint sourcePixelForGlobalPoint(const QPoint& point) const;
    static QPoint mappedSourcePixelForGlobalPoint(
      const CaptureSourceMapping& sourceMapping,
      const QWidget* captureSurface,
      const QPoint& point)
    {
        if (!sourceMapping.isValid() || captureSurface == nullptr) {
            return { -1, -1 };
        }
        const QPoint editorPoint = captureSurface->mapFromGlobal(point);
        if (!sourceMapping.editorBounds().contains(editorPoint)) {
            return { -1, -1 };
        }
        return sourceMapping.sourcePixel(editorPoint);
    }

signals:
    void colorUpdated(const QColor& color);
    void colorGrabbed(const QColor& color);
    void grabAborted();

private:
    bool eventFilter(QObject* obj, QEvent* event) override;
    void paintEvent(QPaintEvent* e) override;
    void showEvent(QShowEvent* event) override;

    QPoint cursorPos() const;
    QColor getColorAtPoint(const QPoint& point) const;
    void setExtraZoomActive(bool active);
    void setMagnifierActive(bool active);
    void updateWidget();
    void finalize();

    QPixmap* m_pixmap;
    CaptureSourceMapping m_sourceMapping;
    QPointer<QWidget> m_captureSurface;
    QImage m_previewImage;
    QColor m_color;

    bool m_mousePressReceived;
    bool m_extraZoomActive;
    bool m_magnifierActive;
};
