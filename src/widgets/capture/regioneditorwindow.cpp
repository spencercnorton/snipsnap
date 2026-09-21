// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Spencer Norton

#include "regioneditorwindow.h"
#include "capturewidget.h"
#include "utils/colorutils.h"

#include <QApplication>
#include <QColorDialog>
#include <QCloseEvent>
#include <QEvent>
#include <QGraphicsProxyWidget>
#include <QGraphicsScene>
#include <QHBoxLayout>
#include <QMetaObject>
#include <QMouseEvent>
#include <QResizeEvent>
#include <QScreen>
#include <QScrollArea>
#include <QScrollBar>
#include <QShortcut>
#include <QToolButton>
#include <QWheelEvent>

#include <algorithm>
#include <cmath>
#include <functional>

namespace {

constexpr qreal MinimumViewScale = 0.01;
constexpr qreal MaximumViewScale = 4.0;
constexpr qreal ZoomStep = 1.25;

} // namespace

RegionEditorWindow::RegionEditorWindow(CaptureWidget* editor, QWidget* parent)
  : QGraphicsView(parent)
  , m_editor(editor)
  , m_canvasSize(editor != nullptr ? editor->size() : QSize())
  , m_outputBoundSize(calculateOutputBoundSize(m_canvasSize))
{
    Q_ASSERT(editor != nullptr);
    Q_ASSERT(!m_canvasSize.isEmpty());
    Q_ASSERT(!m_outputBoundSize.isEmpty());

    setAttribute(Qt::WA_DeleteOnClose);
    setAttribute(Qt::WA_QuitOnClose, false);
    setWindowFlags(Qt::WindowStaysOnTopHint | Qt::FramelessWindowHint |
                   Qt::Tool);
    setFrameShape(QFrame::NoFrame);
    setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    setVerticalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    setAlignment(Qt::AlignCenter);
    setBackgroundBrush(Qt::black);
    setViewportUpdateMode(QGraphicsView::MinimalViewportUpdate);
    setTransformationAnchor(QGraphicsView::NoAnchor);
    setResizeAnchor(QGraphicsView::NoAnchor);

    m_scene = new QGraphicsScene(this);
    setScene(m_scene);
    m_proxy = m_scene->addWidget(editor);
    Q_ASSERT(m_proxy != nullptr);
    m_proxy->setPos(0, 0);
    m_scene->setSceneRect(QRectF(QPointF(0, 0), QSizeF(m_canvasSize)));

    connect(editor, &QObject::destroyed, this, [this]() {
        m_editor = nullptr;
        QMetaObject::invokeMethod(
          this, [this]() { close(); }, Qt::QueuedConnection);
    });

    // Qt's Wayland platform cannot reliably surface CaptureWidget's child
    // toolbar through a QGraphicsProxyWidget. Keep the exact capture/editor
    // coordinate model in the proxy, but expose every annotation tool in a
    // native, palette-aware strip owned by this top-level window.
    m_toolControlsScroll = new QScrollArea(viewport());
    m_toolControlsScroll->setObjectName(
      QStringLiteral("snipsnap-region-editor-tool-scroll"));
    m_toolControlsScroll->setFrameShape(QFrame::NoFrame);
    m_toolControlsScroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAsNeeded);
    m_toolControlsScroll->setVerticalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    m_toolControlsScroll->setWidgetResizable(true);
    m_toolControls = new QWidget(m_toolControlsScroll);
    m_toolControls->setObjectName(
      QStringLiteral("snipsnap-region-editor-tools"));
    auto* toolsLayout = new QHBoxLayout(m_toolControls);
    toolsLayout->setContentsMargins(4, 4, 4, 4);
    toolsLayout->setSpacing(4);

    const QList<CaptureTool::Type> hostTools = {
        CaptureTool::TYPE_PENCIL,      CaptureTool::TYPE_DRAWER,
        CaptureTool::TYPE_ARROW,       CaptureTool::TYPE_SELECTION,
        CaptureTool::TYPE_RECTANGLE,   CaptureTool::TYPE_CIRCLE,
        CaptureTool::TYPE_MARKER,      CaptureTool::TYPE_TEXT,
        CaptureTool::TYPE_PIXELATE,    CaptureTool::TYPE_INVERT,
        CaptureTool::TYPE_CIRCLECOUNT, CaptureTool::TYPE_MOVESELECTION,
        CaptureTool::TYPE_UNDO,        CaptureTool::TYPE_REDO,
    };
    for (const CaptureTool::Type type : hostTools) {
        if (!editor->hasTool(type)) {
            continue;
        }
        auto* button = new QToolButton(m_toolControls);
        button->setCheckable(editor->isSelectableTool(type));
        // CaptureWidget owns exclusivity and deliberately lets the active
        // drawing tool toggle off. The host mirrors that state through
        // activeToolChanged; Qt auto-exclusive buttons cannot represent NONE.
        button->setAutoExclusive(false);
        button->setAutoRaise(false);
        button->setIcon(editor->toolIcon(type, palette().window().color()));
        button->setIconSize(QSize(22, 22));
        button->setToolTip(editor->toolDescription(type));
        button->setAccessibleName(editor->toolDescription(type));
        button->setMinimumSize(36, 36);
        connect(button, &QToolButton::clicked, this, [this, type]() {
            if (m_editor) {
                m_editor->triggerTool(type);
            }
            focusEditor();
        });
        toolsLayout->addWidget(button);
        m_toolButtons.insert(type, button);
    }
    connect(editor,
            &CaptureWidget::activeToolChanged,
            this,
            [this](CaptureTool::Type active) {
                for (auto it = m_toolButtons.cbegin();
                     it != m_toolButtons.cend();
                     ++it) {
                    if (it.value()) {
                        it.value()->setChecked(it.key() == active);
                    }
                }
            });

    m_colorButton = new QToolButton(m_toolControls);
    m_colorButton->setAutoRaise(false);
    m_colorButton->setIconSize(QSize(22, 22));
    m_colorButton->setToolTip(tr("Choose annotation color"));
    m_colorButton->setAccessibleName(tr("Choose annotation color"));
    m_colorButton->setMinimumSize(36, 36);
    connect(m_colorButton, &QToolButton::clicked, this, [this]() {
        if (!m_editor) {
            return;
        }
        const QColor color = QColorDialog::getColor(
          m_editor->toolColor(), this, tr("Annotation color"));
        if (color.isValid() && m_editor) {
            m_editor->setToolColor(color);
        }
        focusEditor();
    });
    toolsLayout->addWidget(m_colorButton);

    const auto addSizeControl =
      [this, toolsLayout](const QString& text,
                          const QString& tooltip,
                          CaptureTool::Type type) {
          if (!m_editor || !m_editor->hasTool(type)) {
              return;
          }
          auto* button = new QToolButton(m_toolControls);
          button->setText(text);
          button->setToolTip(tooltip);
          button->setAccessibleName(tooltip);
          button->setAutoRaise(false);
          button->setMinimumSize(36, 36);
          connect(button, &QToolButton::clicked, this, [this, type]() {
              if (m_editor) {
                  m_editor->triggerTool(type);
              }
              focusEditor();
          });
          toolsLayout->addWidget(button);
      };
    addSizeControl(QString::fromUtf8("−"),
                   tr("Decrease annotation size"),
                   CaptureTool::TYPE_SIZEDECREASE);
    addSizeControl(QStringLiteral("+"),
                   tr("Increase annotation size"),
                   CaptureTool::TYPE_SIZEINCREASE);
    toolsLayout->addStretch(1);
    m_toolControlsScroll->setWidget(m_toolControls);
    refreshToolIcons();

    m_viewControls = new QWidget(viewport());
    m_viewControls->setObjectName(
      QStringLiteral("snipsnap-region-editor-view-controls"));
    auto* controlsLayout = new QHBoxLayout(m_viewControls);
    controlsLayout->setContentsMargins(4, 4, 4, 4);
    controlsLayout->setSpacing(4);
    const auto addControl = [this, controlsLayout](
                              const QString& text,
                              const QString& tooltip,
                              const std::function<void()>& action) {
        auto* button = new QToolButton(m_viewControls);
        button->setText(text);
        button->setToolTip(tooltip);
        button->setAccessibleName(tooltip);
        button->setAutoRaise(false);
        button->setMinimumHeight(32);
        button->setMinimumWidth(text.size() > 1 ? 48 : 32);
        connect(button, &QToolButton::clicked, this, [this, action]() {
            action();
            focusEditor();
        });
        controlsLayout->addWidget(button);
    };
    // Host-owned export controls: the embedded toolbar does not surface on
    // Wayland yet, and without these the only exits are blind shortcuts and
    // a close that discards the capture.
    addControl(tr("Copy"), tr("Copy the capture to the clipboard"), [this]() {
        if (m_editor) {
            m_editor->triggerTool(CaptureTool::TYPE_COPY);
        }
    });
    addControl(tr("Save"), tr("Save the capture to a file"), [this]() {
        if (m_editor) {
            m_editor->triggerTool(CaptureTool::TYPE_SAVE);
        }
    });
    addControl(tr("Fit"), tr("Fit the complete capture (Ctrl+0)"), [this]() {
        fitCanvas(true);
    });
    addControl(tr("1:1"), tr("Show one logical pixel per screen pixel (Ctrl+1)"),
               [this]() {
                   setViewScale(1.0, viewport()->rect().center());
               });
    addControl(QString::fromUtf8("−"), tr("Zoom out (Ctrl+-)"), [this]() {
        setViewScale(m_viewScale / ZoomStep, viewport()->rect().center());
    });
    addControl(QStringLiteral("+"), tr("Zoom in (Ctrl++)"), [this]() {
        setViewScale(m_viewScale * ZoomStep, viewport()->rect().center());
    });
    addControl(
      tr("Hide tools"),
      tr("Hide editor controls so every image pixel is reachable (Tab)"),
      [this]() { setControlsVisible(false); });
    m_viewControls->adjustSize();

    const auto addShortcut = [this](const QKeySequence& sequence,
                                    const std::function<void()>& action) {
        auto* shortcut = new QShortcut(sequence, this);
        shortcut->setContext(Qt::WindowShortcut);
        connect(shortcut, &QShortcut::activated, this, [this, action]() {
            action();
            focusEditor();
        });
    };
    addShortcut(QKeySequence(QStringLiteral("Ctrl+0")),
                [this]() { fitCanvas(true); });
    addShortcut(QKeySequence(QStringLiteral("Ctrl+1")), [this]() {
        setViewScale(1.0, viewport()->rect().center());
    });
    addShortcut(QKeySequence::ZoomIn, [this]() {
        setViewScale(m_viewScale * ZoomStep, viewport()->rect().center());
    });
    addShortcut(QKeySequence::ZoomOut, [this]() {
        setViewScale(m_viewScale / ZoomStep, viewport()->rect().center());
    });
    addShortcut(QKeySequence(Qt::Key_Tab), [this]() {
        setControlsVisible(!m_toolControlsScroll->isVisible());
    });

    m_showControlsButton = new QToolButton(viewport());
    m_showControlsButton->setText(tr("Tools"));
    m_showControlsButton->setToolTip(tr("Show editor controls (Tab)"));
    m_showControlsButton->setAccessibleName(tr("Show editor controls"));
    m_showControlsButton->setAutoRaise(false);
    connect(m_showControlsButton, &QToolButton::clicked, this, [this]() {
        setControlsVisible(true);
        focusEditor();
    });
    m_showControlsButton->hide();

    resize(m_outputBoundSize);
    fitCanvas();
    positionViewControls();
    editor->show();
    focusEditor();
}

QSize RegionEditorWindow::canvasSize() const
{
    return m_canvasSize;
}

QSize RegionEditorWindow::outputBoundSize() const
{
    return m_outputBoundSize;
}

QSize RegionEditorWindow::viewportSize() const
{
    return viewport()->size();
}

qreal RegionEditorWindow::viewScale() const
{
    return m_viewScale;
}

void RegionEditorWindow::changeEvent(QEvent* event)
{
    QGraphicsView::changeEvent(event);
    if (event->type() == QEvent::PaletteChange ||
        event->type() == QEvent::ApplicationPaletteChange ||
        event->type() == QEvent::StyleChange) {
        refreshToolIcons();
    }
}

void RegionEditorWindow::closeEvent(QCloseEvent* event)
{
    if (m_editor) {
        event->ignore();
        m_editor->close();
        return;
    }
    QGraphicsView::closeEvent(event);
}

void RegionEditorWindow::mouseMoveEvent(QMouseEvent* event)
{
    if (!m_panning) {
        QGraphicsView::mouseMoveEvent(event);
        return;
    }
    const QPoint delta = event->position().toPoint() - m_lastPanPosition;
    m_lastPanPosition = event->position().toPoint();
    horizontalScrollBar()->setValue(horizontalScrollBar()->value() - delta.x());
    verticalScrollBar()->setValue(verticalScrollBar()->value() - delta.y());
    event->accept();
}

void RegionEditorWindow::mousePressEvent(QMouseEvent* event)
{
    if (event->button() != Qt::MiddleButton) {
        QGraphicsView::mousePressEvent(event);
        return;
    }
    m_panning = true;
    m_lastPanPosition = event->position().toPoint();
    m_panStartOffset = { horizontalScrollBar()->value(),
                         verticalScrollBar()->value() };
    viewport()->setCursor(Qt::ClosedHandCursor);
    event->accept();
}

void RegionEditorWindow::mouseReleaseEvent(QMouseEvent* event)
{
    if (!m_panning || event->button() != Qt::MiddleButton) {
        QGraphicsView::mouseReleaseEvent(event);
        return;
    }
    m_panning = false;
    viewport()->unsetCursor();
    const QPoint offsetDelta(
      horizontalScrollBar()->value() - m_panStartOffset.x(),
      verticalScrollBar()->value() - m_panStartOffset.y());
    if (!offsetDelta.isNull()) {
        emit viewPanned(qRound(m_viewScale * 1'000'000.0),
                        offsetDelta.x(),
                        offsetDelta.y());
    }
    event->accept();
}

void RegionEditorWindow::resizeEvent(QResizeEvent* event)
{
    QGraphicsView::resizeEvent(event);
    fitCanvas();
    positionViewControls();
}

void RegionEditorWindow::wheelEvent(QWheelEvent* event)
{
    if (!(event->modifiers() & Qt::ControlModifier)) {
        QGraphicsView::wheelEvent(event);
        return;
    }
    const qreal steps = event->angleDelta().y() / 120.0;
    if (steps != 0.0) {
        setViewScale(m_viewScale * std::pow(ZoomStep, steps),
                     event->position().toPoint());
    }
    event->accept();
}

QSize RegionEditorWindow::calculateOutputBoundSize(const QSize& canvasSize)
{
    QSize outputBound;
    for (const QScreen* screen : QGuiApplication::screens()) {
        const QSize candidate = screen->geometry().size();
        if (candidate.isEmpty()) {
            continue;
        }
        if (outputBound.isEmpty()) {
            outputBound = candidate;
        } else {
            outputBound.setWidth(
              std::min(outputBound.width(), candidate.width()));
            outputBound.setHeight(
              std::min(outputBound.height(), candidate.height()));
        }
    }
    return outputBound.isEmpty() ? canvasSize : outputBound;
}

void RegionEditorWindow::fitCanvas(bool announce)
{
    if (!m_scene || m_canvasSize.isEmpty() || viewport()->size().isEmpty()) {
        return;
    }

    m_viewScale = regionEditorFitScale(m_canvasSize, viewport()->size());
    if (!qIsFinite(m_viewScale) || m_viewScale <= 0.0) {
        m_viewScale = 1.0;
    }
    m_viewScale = std::clamp(
      m_viewScale, MinimumViewScale, MaximumViewScale);
    QTransform transform;
    transform.scale(m_viewScale, m_viewScale);
    setTransform(transform);
    centerOn(m_scene->sceneRect().center());
    if (announce) {
        emit viewModeChanged(QStringLiteral("whole_canvas"),
                             qRound(m_viewScale * 1'000'000.0));
    }
}

void RegionEditorWindow::focusEditor()
{
    if (m_proxy) {
        m_proxy->setFocus(Qt::ActiveWindowFocusReason);
    }
}

void RegionEditorWindow::positionViewControls()
{
    if (!m_viewControls || !m_toolControlsScroll || !m_showControlsButton) {
        return;
    }
    constexpr int margin = 8;
    constexpr int controlHeight = 48;
    const int maximumToolWidth =
      std::max(1, viewport()->width() - (margin * 2));
    m_toolControlsScroll->setGeometry(
      margin, margin, maximumToolWidth, controlHeight);
    m_toolControlsScroll->raise();
    m_viewControls->adjustSize();
    m_viewControls->move(
      std::max(margin,
               viewport()->width() - m_viewControls->width() - margin),
      margin + controlHeight + 4);
    m_viewControls->raise();
    m_showControlsButton->adjustSize();
    m_showControlsButton->move(
      margin,
      std::max(margin,
               viewport()->height() - m_showControlsButton->height() - margin));
    m_showControlsButton->raise();
}

void RegionEditorWindow::setControlsVisible(bool visible)
{
    if (!m_toolControlsScroll || !m_viewControls) {
        return;
    }
    m_toolControlsScroll->setVisible(visible);
    m_viewControls->setVisible(visible);
    if (m_showControlsButton) {
        m_showControlsButton->setVisible(!visible);
    }
}

void RegionEditorWindow::refreshToolIcons()
{
    if (!m_editor) {
        return;
    }
    const QColor background = palette().window().color();
    for (auto it = m_toolButtons.cbegin(); it != m_toolButtons.cend(); ++it) {
        if (it.value()) {
            it.value()->setIcon(m_editor->toolIcon(it.key(), background));
        }
    }
    if (m_colorButton) {
        const QString contrast =
          ColorUtils::colorIsDark(background) ? QStringLiteral("white")
                                              : QStringLiteral("black");
        m_colorButton->setIcon(QIcon(QStringLiteral(":/img/material/") +
                                     contrast +
                                     QStringLiteral("/colorize.svg")));
    }
}

void RegionEditorWindow::setViewScale(qreal scale, const QPoint& anchor)
{
    if (!m_scene || !qIsFinite(scale)) {
        return;
    }
    scale = std::clamp(scale, MinimumViewScale, MaximumViewScale);
    const QPointF sceneAnchor = mapToScene(anchor);
    m_viewScale = scale;
    QTransform transform;
    transform.scale(m_viewScale, m_viewScale);
    setTransform(transform);

    const QPointF sceneAnchorAfter = mapToScene(anchor);
    const QPointF sceneCenterAfter = mapToScene(viewport()->rect().center());
    centerOn(regionEditorAnchoredCenter(
      sceneCenterAfter, sceneAnchor, sceneAnchorAfter));
    emit viewModeChanged(qFuzzyCompare(m_viewScale, qreal{ 1.0 })
                           ? QStringLiteral("one_to_one")
                           : QStringLiteral("zoom"),
                         qRound(m_viewScale * 1'000'000.0));
}
