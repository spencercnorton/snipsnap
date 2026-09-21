// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2017-2019 Alejandro Sirgo Rica & Contributors

// Based on Lightscreen areadialog.h, Copyright 2017  Christian Kaiser
// <info@ckaiser.com.ar> released under the GNU GPL2
// <https://www.gnu.org/licenses/gpl-2.0.txt>

// Based on KDE's KSnapshot regiongrabber.cpp, revision 796531, Copyright 2007
// Luca Gugelmann <lucag@student.ethz.ch> released under the GNU LGPL
// <http://www.gnu.org/licenses/old-licenses/library.txt>

#pragma once

#include "tools/capturecontext.h"
#include "tools/capturesourcemapping.h"
#include "tools/capturetool.h"
#include "utils/confighandler.h"
#include "widgets/capture/buttonhandler.h"
#include "widgets/capture/capturetoolbutton.h"
#include "widgets/capture/capturetoolobjects.h"
#include "widgets/capture/magnifierwidget.h"
#include "widgets/capture/selectionwidget.h"

#include <QMessageBox>
#include <QIcon>
#include <QMap>
#include <QPointer>
#include <QTimer>
#include <QUndoStack>
#include <QWidget>

class QLabel;
class QPaintEvent;
class QResizeEvent;
class QMouseEvent;
class QShortcut;
class QNetworkAccessManager;
class QNetworkReply;
class ColorPicker;
class NotifierBox;
class HoverEventFilter;
#if !defined(DISABLE_UPDATE_CHECKER)
class UpdateNotificationWidget;
#endif
class UtilityPanel;
class SidePanelWidget;

class CaptureWidget : public QWidget
{
    Q_OBJECT

public:
    struct RegionEditorSource
    {
        QRect logicalSourceRect;
    };

    explicit CaptureWidget(const CaptureRequest& req,
                           const QPixmap& screenshot,
                           int selectedMonitor,
                           bool fullScreen = true,
                           QWidget* parent = nullptr);
    explicit CaptureWidget(const CaptureRequest& req,
                           const QPixmap& screenshot,
                           const RegionEditorSource& source,
                           QWidget* parent = nullptr);
    ~CaptureWidget();

    QPixmap pixmap();
    void setCaptureToolObjects(const CaptureToolObjects& captureToolObjects);
    // Trigger any editor tool from a host window whose own controls provably
    // render. RegionEditorWindow uses this to expose the complete annotation
    // tool set even when Qt cannot composite child buttons through a
    // QGraphicsProxyWidget on Wayland.
    void triggerTool(CaptureTool::Type type);
    bool hasTool(CaptureTool::Type type) const;
    bool isSelectableTool(CaptureTool::Type type) const;
    QIcon toolIcon(CaptureTool::Type type, const QColor& background) const;
    QString toolDescription(CaptureTool::Type type) const;
    QColor toolColor() const;
    void setToolColor(const QColor& color);
#if !defined(DISABLE_UPDATE_CHECKER)
    void showAppUpdateNotification(const QString& appLatestVersion,
                                   const QString& appLatestUrl);
#endif

public slots:
    bool commitCurrentTool();
    void deleteToolWidgetOrClose();

signals:
    void colorChanged(const QColor& c);
    void toolSizeChanged(int size);
    void activeToolChanged(CaptureTool::Type type);
    // Emitted once on the event-loop turn after the first complete paint. It
    // is an application-side readiness commitment, not a Wayland presentation
    // timestamp.
    void firstPaintCompleted();

private slots:
    void undo();
    void redo();
    void cancel();
    void togglePanel();
    void childEnter();
    void childLeave();

    void deleteCurrentTool();

    void setState(CaptureToolButton* b);
    void handleToolSignal(CaptureTool::Request r);
    void handleButtonLeftClick(CaptureToolButton* b);
    void handleButtonRightClick(CaptureToolButton* b);
    void setDrawColor(const QColor& c);
    void onToolSizeChanged(int size);
    void onToolSizeSettled(int size);
    void updateActiveLayer(int layer);
    void onMoveCaptureToolUp(int captureToolIndex);
    void onMoveCaptureToolDown(int captureToolIndex);
    void selectAll();
    void xywhTick();
    void onDisplayGridChanged(bool display);
    void onGridSizeChanged(int size);

    void startColorGrab();

public:
    void removeToolObject(int index = -1);
    void showxywh();

protected:
    void paintEvent(QPaintEvent* paintEvent) override;
    void mousePressEvent(QMouseEvent* mouseEvent) override;
    void mouseMoveEvent(QMouseEvent* mouseEvent) override;
    void mouseReleaseEvent(QMouseEvent* mouseEvent) override;
    void mouseDoubleClickEvent(QMouseEvent* event) override;
    void keyPressEvent(QKeyEvent* keyEvent) override;
    void keyReleaseEvent(QKeyEvent* keyEvent) override;
    void wheelEvent(QWheelEvent* wheelEvent) override;
    void resizeEvent(QResizeEvent* resizeEvent) override;
    void moveEvent(QMoveEvent* moveEvent) override;
    void changeEvent(QEvent* changeEvent) override;
    void closeEvent(QCloseEvent* event) override;

private:
    CaptureWidget(const CaptureRequest& req,
                  const QPixmap& screenshot,
                  int selectedMonitor,
                  bool fullScreen,
                  const RegionEditorSource* regionSource,
                  QWidget* parent);
    void pushObjectsStateToUndoStack();
    void releaseActiveTool();
    void uncheckActiveTool();
    int selectToolItemAtPos(const QPoint& pos);
    void showColorPicker(const QPoint& pos);
    bool startDrawObjectTool(const QPoint& pos);
    QPointer<CaptureTool> activeToolObject();
    void initContext(bool fullscreen, const CaptureRequest& req);
    void initPanel();
    void initSelection();
    void initShortcuts();
    void initButtons();
    void initHelpMessage();
    void initQuitPrompt();
    void updateSizeIndicator();
    void updateCursor();
    void updateSelectionState();
    void updateTool(CaptureTool* tool);
    void updateLayersPanel();
    bool promptQuit();
    void pushToolToStack();
    void makeChild(QWidget* w);
    void restoreCircleCountState();

    QList<QShortcut*> newShortcut(const QKeySequence& key,
                                  QWidget* parent,
                                  const char* slot);

    void setToolSize(int size);

    QRect extendedSelection() const;
    QRect extendedRect(const QRect& r) const;
    QRect paddedUpdateRect(const QRect& r) const;
    void drawErrorMessage(const QString& msg, QPainter* painter);
    void drawInactiveRegion(QPainter* painter);
    void drawToolsData(bool drawSelection = true);
    void drawObjectSelection();

    void processPixmapWithTool(QPixmap* pixmap, CaptureTool* tool);

    CaptureTool* activeButtonTool() const;
    CaptureTool::Type activeButtonToolType() const;

    QPoint snapToGrid(const QPoint& point) const;

    ////////////////////////////////////////
    // Class members

    // Context information
    CaptureContext m_context;
    CaptureSourceMapping m_sourceMapping;

    // Main ui color
    QColor m_uiColor;
    // Secondary ui color
    QColor m_contrastUiColor;

    // Outside selection opacity
    int m_opacity;
    int m_toolSizeByKeyboard;

    // utility flags
    bool m_mouseIsClicked;
    bool m_newSelection;
    bool m_movingSelection;
    bool m_captureDone;
    bool m_regionEditor;
    bool m_previewEnabled;
    bool m_adjustmentButtonPressed;
    bool m_configError;
    bool m_configErrorResolved;

#if !defined(DISABLE_UPDATE_CHECKER)
    UpdateNotificationWidget* m_updateNotificationWidget;
#endif
    quint64 m_lastMouseWheel;
    QPointer<CaptureToolButton> m_sizeIndButton;
    // Last pressed button
    QPointer<CaptureToolButton> m_activeButton;
    QPointer<CaptureTool> m_activeTool;
    bool m_activeToolIsMoved;
    QPointer<QWidget> m_toolWidget;
    QPointer<QMessageBox> m_quitPrompt;

    ButtonHandler* m_buttonHandler;
    UtilityPanel* m_panel;
    SidePanelWidget* m_sidePanel;
    ColorPicker* m_colorPicker;
    ConfigHandler m_config;
    NotifierBox* m_notifierBox;
    HoverEventFilter* m_eventFilter;
    SelectionWidget* m_selection;
    MagnifierWidget* m_magnifier;
    QString m_helpMessage;

    SelectionWidget::SideType m_mouseOverHandle;

    QMap<CaptureTool::Type, CaptureTool*> m_tools;
    QMap<CaptureTool::Type, QPointer<CaptureToolButton>> m_toolButtons;
    CaptureToolObjects m_captureToolObjects;
    CaptureToolObjects m_captureToolObjectsBackup;

    QPoint m_mousePressedPos;
    QPoint m_activeToolOffsetToMouseOnStart;

    // XYWH display position and timer
    bool m_xywhDisplay;
    QTimer m_xywhTimer;

    QUndoStack m_undoStack;

    bool m_existingObjectIsChanged;

    // For start moving after more than X offset
    QPoint m_startMovePos;
    bool m_startMove;

    // Grid
    bool m_displayGrid{ false };
    int m_gridSize{ 10 };

    bool m_clipboardWorkaroundDone{ false };
    bool m_firstPaintCompletionScheduled{ false };
};
