// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#pragma once

#include "regioneditorviewport.h"
#include "tools/capturetool.h"

#include <QGraphicsView>
#include <QMap>
#include <QPointer>
#include <QSize>

class CaptureWidget;
class QCloseEvent;
class QEvent;
class QGraphicsProxyWidget;
class QGraphicsScene;
class QScrollArea;
class QMouseEvent;
class QResizeEvent;
class QToolButton;
class QWheelEvent;
class QWidget;

/**
 * Native host for a potentially multi-output CaptureWidget canvas.
 *
 * QGraphicsProxyWidget maps painting, input, child widgets, and focus through
 * one uniform view transform. CaptureWidget therefore continues to operate in
 * the compositor selection's exact logical coordinate space even when Mutter
 * constrains this top-level to a single output.
 */
class RegionEditorWindow final : public QGraphicsView
{
    Q_OBJECT

public:
    explicit RegionEditorWindow(CaptureWidget* editor,
                                QWidget* parent = nullptr);

    QSize canvasSize() const;
    QSize outputBoundSize() const;
    QSize viewportSize() const;
    qreal viewScale() const;

signals:
    void viewModeChanged(const QString& mode, int scalePpm);
    void viewPanned(int scalePpm, int offsetDeltaX, int offsetDeltaY);

protected:
    void changeEvent(QEvent* event) override;
    void closeEvent(QCloseEvent* event) override;
    void mouseMoveEvent(QMouseEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;
    void resizeEvent(QResizeEvent* event) override;
    void wheelEvent(QWheelEvent* event) override;

private:
    static QSize calculateOutputBoundSize(const QSize& canvasSize);
    void fitCanvas(bool announce = false);
    void focusEditor();
    void positionViewControls();
    void refreshToolIcons();
    void setControlsVisible(bool visible);
    void setViewScale(qreal scale, const QPoint& anchor);

    QPointer<CaptureWidget> m_editor;
    QGraphicsScene* m_scene{ nullptr };
    QGraphicsProxyWidget* m_proxy{ nullptr };
    QWidget* m_toolControls{ nullptr };
    QScrollArea* m_toolControlsScroll{ nullptr };
    QWidget* m_viewControls{ nullptr };
    QMap<CaptureTool::Type, QPointer<QToolButton>> m_toolButtons;
    QPointer<QToolButton> m_colorButton;
    QPointer<QToolButton> m_showControlsButton;
    QSize m_canvasSize;
    QSize m_outputBoundSize;
    qreal m_viewScale{ 1.0 };
    QPoint m_lastPanPosition;
    QPoint m_panStartOffset;
    bool m_panning{ false };
};
