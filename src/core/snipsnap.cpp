// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2017-2019 Alejandro Sirgo Rica & Contributors

#include "snipsnap.h"
#include "core/snipsnapdaemon.h"
#if defined(Q_OS_MACOS) || defined(Q_OS_WIN)
#include "qhotkey.h"
#endif

#if defined(Q_OS_MACOS)
#include <QWindow>
#include <objc/message.h>

namespace {

constexpr long NSApplicationActivationPolicyRegular = 0;
constexpr long NSApplicationActivationPolicyAccessory = 1;

void setActivationPolicy(long policy)
{
    auto sharedApp = reinterpret_cast<id (*)(id, SEL)>(objc_msgSend);
    auto setPolicy = reinterpret_cast<void (*)(id, SEL, long)>(objc_msgSend);
    id nsApp = sharedApp(reinterpret_cast<id>(objc_getClass("NSApplication")),
                         sel_registerName("sharedApplication"));
    setPolicy(nsApp, sel_registerName("setActivationPolicy:"), policy);
}

void setActivationPolicyRegular()
{
    setActivationPolicy(NSApplicationActivationPolicyRegular);
}

void setActivationPolicyAccessory()
{
    setActivationPolicy(NSApplicationActivationPolicyAccessory);
}

constexpr const char* visibleInDockProperty = "_visibleInDock";

} // namespace

#include <CoreGraphics/CoreGraphics.h>
#endif

#include "config/configresolver.h"
#include "config/configwindow.h"
#include "core/qguiappcurrentscreen.h"
#include "utils/abstractlogger.h"
#include "utils/capturetrace.h"
#include "utils/confighandler.h"
#include "utils/screengrabber.h"
#include "utils/screenshotsaver.h"
#include "widgets/capture/capturewidget.h"
#include "widgets/capture/regioneditorwindow.h"
#include "widgets/capturelauncher.h"
#include "widgets/infowindow.h"

#ifdef ENABLE_IMGUR
#include "tools/imgupload/imguploadermanager.h"
#include "tools/imgupload/storages/imguploaderbase.h"
#include "widgets/imguploaddialog.h"
#include "widgets/uploadhistory.h"
#endif

#include <QApplication>
#include <QBuffer>
#include <QDebug>
#include <QDesktopServices>
#include <QFile>
#include <QImage>
#include <QMessageBox>
#include <QThread>
#include <QTimer>
#include <QUrl>
#include <QVersionNumber>

#if defined(Q_OS_MACOS)
#include <QScreen>
#endif

SnipSnap::SnipSnap()
  : m_haveExternalWidget(false)
  , m_captureWindow(nullptr)
#if (defined(Q_OS_MACOS) || defined(Q_OS_WIN))
  , m_HotkeyScreenshotCapture(nullptr)
#endif
#if (defined(Q_OS_MACOS) && ENABLE_IMGUR)
  , m_HotkeyScreenshotHistory(nullptr)
#endif
{
    QString StyleSheet = CaptureButton::globalStyleSheet();
    qApp->setStyleSheet(StyleSheet);

#if defined(Q_OS_MACOS)
    // Request Screen Recording permission via the proper CoreGraphics API
    if (!CGPreflightScreenCaptureAccess()) {
        CGRequestScreenCaptureAccess();
    }
#endif
#if (defined(Q_OS_MACOS) || defined(Q_OS_WIN))
    // Set global shortcuts for MacOS or Windows
    m_HotkeyScreenshotCapture = new QHotkey(
      QKeySequence(ConfigHandler().shortcut("TAKE_SCREENSHOT")), true, this);
    QObject::connect(m_HotkeyScreenshotCapture,
                     &QHotkey::activated,
                     qApp,
                     [this]() { gui(); });
#endif
#if (defined(Q_OS_MACOS) && ENABLE_IMGUR)
    m_HotkeyScreenshotHistory = new QHotkey(
      QKeySequence(ConfigHandler().shortcut("SCREENSHOT_HISTORY")), true, this);
    QObject::connect(m_HotkeyScreenshotHistory,
                     &QHotkey::activated,
                     qApp,
                     [this]() { history(); });
#endif
}

SnipSnap* SnipSnap::instance()
{
    static SnipSnap c;
    return &c;
}

void SnipSnap::gui(const CaptureRequest& req)
{
    const CaptureTrace::TimelineStart traceStart =
      CaptureTrace::captureRequestStart();
    if (!reserveCapture(true)) {
        return;
    }
    if (!resolveAnyConfigErrors()) {
        m_captureRequestPending = false;
        return;
    }
    replaceGraphicalWindow();
    (void)CaptureTrace::begin(QStringLiteral("gui_direct"), traceStart);
    CaptureTrace::mark(QStringLiteral("capture_request_accepted"));
    startGuiCapture(req);
}

void SnipSnap::startGuiCapture(const CaptureRequest& req)
{
    CaptureTrace::mark(QStringLiteral("gui_capture_started"));
    if (nullptr == m_captureWindow && nullptr == m_activeGrabber) {
        // TODO is this unnecessary now?
        int timeout = 5000; // 5 seconds
        const int delay = 100;
        QWidget* modalWidget = nullptr;
        for (; timeout >= 0; timeout -= delay) {
            modalWidget = qApp->activeModalWidget();
            if (nullptr == modalWidget) {
                break;
            }
            modalWidget->close();
            modalWidget->deleteLater();
            QThread::msleep(delay);
        }
        if (0 == timeout) {
            QMessageBox::warning(
              nullptr, tr("Error"), tr("Unable to close active modal widgets"));
            failAcceptedCapture();
            return;
        }

        m_activeGrabber = new ScreenGrabber(this);
        ScreenGrabber* grabber = m_activeGrabber;
        // The active grabber now owns exclusion for the accepted request.
        m_captureRequestPending = false;
        connect(grabber,
                &ScreenGrabber::captureFinished,
                this,
                [this, grabber, req](const QPixmap& screenshot, bool ok) {
                    const int selectedMonitor = grabber->getSelectedMonitor();
                    CaptureTrace::mark(
                      QStringLiteral("screen_grab_finished"),
                      { { QStringLiteral("ok"), ok },
                        { QStringLiteral("selected_monitor"),
                          selectedMonitor },
                        { QStringLiteral("width"), screenshot.width() },
                        { QStringLiteral("height"), screenshot.height() } });
                    if (!ok || screenshot.isNull()) {
                        m_activeGrabber = nullptr;
                        grabber->deleteLater();
                        CaptureTrace::finish(
                          QStringLiteral("capture_failed"),
                          { { QStringLiteral("reason"),
                              QStringLiteral("screen_grab") } });
                        emit captureFailed();
                        return;
                    }

                    CaptureTrace::mark(
                      QStringLiteral("capture_widget_construction_started"));
                    m_captureWindow =
                      new CaptureWidget(req, screenshot, selectedMonitor);
                    CaptureTrace::mark(
                      QStringLiteral("capture_widget_constructed"));
                    // Keep the grabber as the busy owner until window
                    // construction has completed and m_captureWindow is set.
                    m_activeGrabber = nullptr;
                    grabber->deleteLater();
#ifdef Q_OS_WIN
                    m_captureWindow->show();
#elif defined(Q_OS_MACOS)
                    if (ConfigHandler().useNativeFullscreen()) {
                        m_captureWindow->showFullScreen();
                    } else {
                        m_captureWindow->show();
                    }
                    m_captureWindow->activateWindow();
                    m_captureWindow->raise();
#else
                    m_captureWindow->showFullScreen();
//                  m_captureWindow->show(); // CaptureWidget debugging
#endif
                    CaptureTrace::mark(
                      QStringLiteral("capture_widget_show_requested"));
                    emit captureWindowCreated(m_captureWindow);
                });

        int selectedMonitor = -1;
        if (req.hasSelectedMonitor()) {
            selectedMonitor = req.selectedMonitor();
        }
        CaptureTrace::mark(
          QStringLiteral("screen_grab_requested"),
          { { QStringLiteral("monitor_count"), qApp->screens().size() },
            { QStringLiteral("preselected_monitor"), selectedMonitor } });
        if (!grabber->grabEntireDesktopAsync(selectedMonitor)) {
            m_activeGrabber = nullptr;
            grabber->deleteLater();
            CaptureTrace::finish(
              QStringLiteral("capture_failed"),
              { { QStringLiteral("reason"),
                  QStringLiteral("screen_grab_rejected") } });
            emit captureFailed();
        }
        // Capture completes asynchronously; captureWindowCreated announces
        // the constructed widget to callers that need it.
        return;
    } else {
        failAcceptedCapture();
        return;
    }
}

CaptureWidget* SnipSnap::openShellBridgeRegion(
  const QImage& image,
  const QRect& sourceLogicalRect,
  qreal devicePixelRatio,
  quint64 bridgeCaptureId)
{
    if (image.isNull() || sourceLogicalRect.isEmpty() ||
        !qIsFinite(devicePixelRatio) || devicePixelRatio < 0.5 ||
        devicePixelRatio > 4.0) {
        return nullptr;
    }
    if (!reserveCapture()) {
        return nullptr;
    }
    // The Shell overlay still owns a modal compositor grab during this call.
    // Never enter ConfigResolver::exec(): the dialog would be inaccessible
    // behind that overlay and its nested event loop could revive a stale
    // handoff after the client disconnects. Activation tooling must repair an
    // invalid configuration before enabling the bridge; runtime fails closed.
    ConfigHandler config;
    if (!config.checkUnrecognizedSettings() || !config.checkSemantics()) {
        AbstractLogger::warning()
          << tr("GNOME Shell bridge refused an invalid SnipSnap configuration");
        m_captureRequestPending = false;
        return nullptr;
    }

    QPixmap screenshot = QPixmap::fromImage(image);
    if (screenshot.isNull()) {
        failAcceptedCapture();
        return nullptr;
    }
    screenshot.setDevicePixelRatio(devicePixelRatio);

    CaptureRequest request(CaptureRequest::GRAPHICAL_MODE);
    request.setInitialSelection({});
    m_captureWindow = new CaptureWidget(
      request,
      screenshot,
      CaptureWidget::RegionEditorSource{ sourceLogicalRect });
    m_captureRequestPending = false;

    const QString editorTitle =
      QStringLiteral("SnipSnap [capture-id=%1]")
        .arg(bridgeCaptureId);
    m_captureWindow->setObjectName(
      QStringLiteral("snipsnap-shell-bridge-editor-canvas"));
    m_captureWindow->setWindowTitle(editorTitle);

    auto* editorWindow = new RegionEditorWindow(m_captureWindow);
    m_captureHostWindow = editorWindow;
    editorWindow->setObjectName(
      QStringLiteral("snipsnap-shell-bridge-editor"));
    editorWindow->setWindowTitle(editorTitle);
    connect(m_captureWindow,
            &CaptureWidget::firstPaintCompleted,
            editorWindow,
            [editorWindow]() {
                const QSize canvas = editorWindow->canvasSize();
                const QSize outputBound = editorWindow->outputBoundSize();
                const QSize viewport = editorWindow->viewportSize();
                CaptureTrace::mark(
                  QStringLiteral("region_editor_view_configured"),
                  { { QStringLiteral("fit_mode"),
                      QStringLiteral("whole_canvas") },
                    { QStringLiteral("canvas_width"), canvas.width() },
                    { QStringLiteral("canvas_height"), canvas.height() },
                    { QStringLiteral("output_bound_width"),
                      outputBound.width() },
                    { QStringLiteral("output_bound_height"),
                      outputBound.height() },
                    { QStringLiteral("window_width"),
                      editorWindow->width() },
                    { QStringLiteral("window_height"),
                      editorWindow->height() },
                    { QStringLiteral("viewport_width"), viewport.width() },
                    { QStringLiteral("viewport_height"), viewport.height() },
                    { QStringLiteral("view_scale_ppm"),
                      qRound(editorWindow->viewScale() * 1'000'000.0) } });
            });
    connect(editorWindow,
            &RegionEditorWindow::viewModeChanged,
            editorWindow,
            [editorWindow](const QString& mode, int scalePpm) {
                const QSize viewport = editorWindow->viewportSize();
                CaptureTrace::mark(
                  QStringLiteral("region_editor_view_changed"),
                  { { QStringLiteral("mode"), mode },
                    { QStringLiteral("view_scale_ppm"), scalePpm },
                    { QStringLiteral("viewport_width"), viewport.width() },
                    { QStringLiteral("viewport_height"), viewport.height() } });
            });
    connect(editorWindow,
            &RegionEditorWindow::viewPanned,
            editorWindow,
            [editorWindow](int scalePpm,
                           int offsetDeltaX,
                           int offsetDeltaY) {
                const QSize viewport = editorWindow->viewportSize();
                CaptureTrace::mark(
                  QStringLiteral("region_editor_view_panned"),
                  { { QStringLiteral("view_scale_ppm"), scalePpm },
                    { QStringLiteral("offset_delta_x"), offsetDeltaX },
                    { QStringLiteral("offset_delta_y"), offsetDeltaY },
                    { QStringLiteral("viewport_width"), viewport.width() },
                    { QStringLiteral("viewport_height"), viewport.height() } });
            });
    editorWindow->show();
    editorWindow->activateWindow();
    editorWindow->raise();
    m_captureWindow->setFocus(Qt::ActiveWindowFocusReason);
    CaptureTrace::mark(QStringLiteral("capture_widget_show_requested"));
    emit captureWindowCreated(m_captureWindow);
    return m_captureWindow;
}

void SnipSnap::screen(CaptureRequest req, const int screenNumber)
{
    if (!reserveCapture()) {
        return;
    }
    if (!resolveAnyConfigErrors()) {
        m_captureRequestPending = false;
        return;
    }
    startScreenCapture(req, screenNumber);
}

void SnipSnap::startScreenCapture(CaptureRequest req, const int screenNumber)
{
    if (m_activeGrabber || m_captureWindow) {
        failAcceptedCapture();
        return;
    }
    if (screenNumber >= qApp->screens().count()) {
        AbstractLogger() << QObject::tr(
          "Requested screen exceeds screen count");
        failAcceptedCapture();
        return;
    }

    m_activeGrabber = new ScreenGrabber(this);
    ScreenGrabber* grabber = m_activeGrabber;
    // The active grabber now owns exclusion for the accepted request.
    m_captureRequestPending = false;
    connect(grabber,
            &ScreenGrabber::captureFinished,
            this,
            [this, grabber, req](const QPixmap& captured, bool ok) mutable {
                QPixmap screenshot = captured;
                QScreen* selectedScreen = grabber->getSelectedScreen();
                if (!ok || !selectedScreen) {
                    m_activeGrabber = nullptr;
                    grabber->deleteLater();
                    emit captureFailed();
                    return;
                }

                QRect geometry = ScreenGrabber().screenGeometry(selectedScreen);
                QRect region = req.initialSelection();
                if (region.isNull()) {
                    region = geometry;
                } else {
                    QRect screenGeom = geometry;
                    screenGeom.moveTopLeft({ 0, 0 });
                    region = region.intersected(screenGeom);
                    screenshot = screenshot.copy(region);
                }
                if (req.tasks() & CaptureRequest::PIN) {
                    req.addPinTask(region);
                }
                exportCapture(screenshot, geometry, req);
                // Keep ownership through dialogs and captureTaken emission so
                // another request cannot consume this request's completion.
                m_activeGrabber = nullptr;
                grabber->deleteLater();
            });

    const bool started =
      screenNumber < 0
        ? grabber->grabEntireDesktopAsync()
        : grabber->grabScreenAsync(qApp->screens().at(screenNumber));
    if (!started) {
        m_activeGrabber = nullptr;
        grabber->deleteLater();
        emit captureFailed();
    }
}

void SnipSnap::full(const CaptureRequest& req)
{
    if (!reserveCapture()) {
        return;
    }
    if (!resolveAnyConfigErrors()) {
        m_captureRequestPending = false;
        return;
    }
    startFullCapture(req);
}

void SnipSnap::startFullCapture(const CaptureRequest& req)
{
    if (m_activeGrabber || m_captureWindow) {
        failAcceptedCapture();
        return;
    }

    m_activeGrabber = new ScreenGrabber(this);
    ScreenGrabber* grabber = m_activeGrabber;
    // The active grabber now owns exclusion for the accepted request.
    m_captureRequestPending = false;
    connect(grabber,
            &ScreenGrabber::captureFinished,
            this,
            [this, grabber, req](const QPixmap& screenshot, bool ok) {
                if (!ok) {
                    m_activeGrabber = nullptr;
                    grabber->deleteLater();
                    emit captureFailed();
                    return;
                }
                QRect selection;
                exportCapture(screenshot, selection, req);
                // Keep ownership through dialogs and captureTaken emission so
                // another request cannot consume this request's completion.
                m_activeGrabber = nullptr;
                grabber->deleteLater();
            });
    if (!grabber->grabFullDesktopAsync()) {
        m_activeGrabber = nullptr;
        grabber->deleteLater();
        emit captureFailed();
    }
}

void SnipSnap::launcher()
{
    if (!resolveAnyConfigErrors()) {
        return;
    }

    if (m_launcherWindow == nullptr) {
        m_launcherWindow = new CaptureLauncher();
    }
    m_launcherWindow->show();
#if defined(Q_OS_MACOS)
    showDockIcon(m_launcherWindow);
#endif
}

void SnipSnap::config()
{
    if (!resolveAnyConfigErrors()) {
        return;
    }

    if (m_configWindow == nullptr) {
        m_configWindow = new ConfigWindow();
        m_configWindow->show();
        // Call show() first, otherwise the correct geometry cannot be fetched
        // for centering the window on the screen
        QRect position = m_configWindow->frameGeometry();
        QScreen* currentScreen = QGuiAppCurrentScreen().currentScreen();
        position.moveCenter(currentScreen->availableGeometry().center());
        m_configWindow->move(position.topLeft());
#if defined(Q_OS_MACOS)
        showDockIcon(m_configWindow);
#endif
    }
}

void SnipSnap::info()
{
    if (m_infoWindow == nullptr) {
        m_infoWindow = new InfoWindow();
#if defined(Q_OS_MACOS)
        showDockIcon(m_infoWindow);
#endif
    }
}

#ifdef ENABLE_IMGUR
void SnipSnap::history()
{
    static UploadHistory* historyWidget = nullptr;
    if (historyWidget == nullptr) {
        historyWidget = new UploadHistory;
        historyWidget->loadHistory();
        connect(historyWidget, &QObject::destroyed, this, []() {
            historyWidget = nullptr;
        });
    }

    historyWidget->show();
    // Call show() first, otherwise the correct geometry cannot be fetched
    // for centering the window on the screen
    QRect position = historyWidget->frameGeometry();
    QScreen* currentScreen = QGuiAppCurrentScreen().currentScreen();
    position.moveCenter(currentScreen->availableGeometry().center());
    historyWidget->move(position.topLeft());

#if defined(Q_OS_MACOS)
    showDockIcon(historyWidget);
#endif
}
#endif

#if defined(Q_OS_MACOS)
void SnipSnap::onWindowVisibilityChanged(QWindow::Visibility newVisibility)
{
    auto* qw = qobject_cast<QWindow*>(sender());
    if (!qw) {
        return;
    }

    if (newVisibility == QWindow::Hidden) {
        qw->setProperty(visibleInDockProperty, false);
        --m_dockIconVisibleCount;
        if (m_dockIconVisibleCount == 0) {
            setActivationPolicyAccessory();
        }
    } else {
        bool windowTrackedInDock = qw->property(visibleInDockProperty).toBool();
        if (!windowTrackedInDock) {
            qw->setProperty(visibleInDockProperty, true);
            ++m_dockIconVisibleCount;
            setActivationPolicyRegular();
        }
    }
}

void SnipSnap::showDockIcon(QWidget* w)
{
    QWindow* qw = w->windowHandle();
    if (!qw) {
        return;
    }

    connect(qw,
            &QWindow::visibilityChanged,
            this,
            &SnipSnap::onWindowVisibilityChanged);
}
#endif

void SnipSnap::openSavePath()
{
    QString savePath = ConfigHandler().savePath();
    if (!savePath.isEmpty()) {
        QDesktopServices::openUrl(QUrl::fromLocalFile(savePath));
    }
}

QVersionNumber SnipSnap::getVersion()
{
    return QVersionNumber::fromString(
      QStringLiteral(APP_VERSION).replace("v", ""));
}

void SnipSnap::setOrigin(Origin origin)
{
    m_origin = origin;
}

SnipSnap::Origin SnipSnap::origin()
{
    return m_origin;
}

/**
 * @brief Prompt the user to resolve config errors if necessary.
 * @return Whether errors were resolved.
 */
bool SnipSnap::resolveAnyConfigErrors()
{
    bool resolved = true;
    ConfigHandler confighandler;
    if (!confighandler.checkUnrecognizedSettings() ||
        !confighandler.checkSemantics()) {
        auto* resolver = new ConfigResolver();
        QObject::connect(
          resolver, &ConfigResolver::rejected, [resolver, &resolved]() {
              resolved = false;
              resolver->deleteLater();
              if (origin() == CLI) {
                  exit(1);
              }
          });
        QObject::connect(
          resolver, &ConfigResolver::accepted, [resolver, &resolved]() {
              resolved = true;
              resolver->close();
              resolver->deleteLater();
              // Ensure that the dialog is closed before starting capture
              qApp->processEvents();
          });
        resolver->exec();
        qApp->processEvents();
    }
    return resolved;
}

bool SnipSnap::reserveCapture(bool replaceGraphicalWindow)
{
    if (m_captureRequestPending || m_activeGrabber) {
        AbstractLogger::warning()
          << tr("Screenshot already in progress; ignoring the new request");
        return false;
    }
#if defined(Q_OS_MACOS)
    const bool canReplaceWindow = replaceGraphicalWindow && m_captureWindow;
#else
    Q_UNUSED(replaceGraphicalWindow)
    constexpr bool canReplaceWindow = false;
#endif
    if (m_captureWindow && !canReplaceWindow) {
        AbstractLogger::warning()
          << tr("Screenshot already in progress; ignoring the new request");
        return false;
    }
    m_captureRequestPending = true;
    return true;
}

void SnipSnap::replaceGraphicalWindow()
{
#if defined(Q_OS_MACOS)
    if (m_captureWindow) {
        // Configuration is already accepted and the pending reservation blocks
        // reentrant requests while the old window emits its terminal signal.
        m_captureWindow->close();
        delete m_captureWindow;
        m_captureWindow = nullptr;
    }
#endif
}

void SnipSnap::failAcceptedCapture()
{
    m_captureRequestPending = false;
    CaptureTrace::finish(QStringLiteral("capture_failed"));
    emit captureFailed();
}

bool SnipSnap::requestCapture(const CaptureRequest& request)
{
    switch (request.captureMode()) {
        case CaptureRequest::FULLSCREEN_MODE:
        case CaptureRequest::SCREEN_MODE:
        case CaptureRequest::GRAPHICAL_MODE:
            break;
        default:
            AbstractLogger::error() << tr("Invalid screenshot capture mode");
            return false;
    }

    const CaptureTrace::TimelineStart traceStart =
      request.captureMode() == CaptureRequest::GRAPHICAL_MODE
        ? CaptureTrace::captureRequestStart()
        : CaptureTrace::TimelineStart{};
    if (!reserveCapture(request.captureMode() ==
                        CaptureRequest::GRAPHICAL_MODE)) {
        return false;
    }
    if (!resolveAnyConfigErrors()) {
        m_captureRequestPending = false;
        return false;
    }
    if (request.captureMode() == CaptureRequest::GRAPHICAL_MODE) {
        // Replace before returning so new request listeners are not connected
        // while the old macOS capture window emits its terminal signal.
        replaceGraphicalWindow();
        (void)CaptureTrace::begin(
          QStringLiteral("request_capture"), traceStart);
        CaptureTrace::mark(QStringLiteral("capture_request_accepted"));
    }

    switch (request.captureMode()) {
        case CaptureRequest::FULLSCREEN_MODE:
            QTimer::singleShot(request.delay(), this, [this, request] {
                startFullCapture(request);
            });
            break;
        case CaptureRequest::SCREEN_MODE: {
            int&& number = request.data().toInt();
            QTimer::singleShot(
              request.delay(), this, [this, request, number]() {
                  startScreenCapture(request, number);
              });
            break;
        }
        case CaptureRequest::GRAPHICAL_MODE: {
            QTimer::singleShot(request.delay(), this, [this, request]() {
                startGuiCapture(request);
            });
            break;
        }
        default:
            Q_UNREACHABLE();
    }
    return true;
}

void SnipSnap::exportCapture(const QPixmap& capture,
                              QRect& selection,
                              const CaptureRequest& req)
{
    using CR = CaptureRequest;
    int tasks = req.tasks(), mode = req.captureMode();
    QString path = req.path();

    if (tasks & CR::PRINT_GEOMETRY) {
        QTextStream(stdout)
          << selection.width() << "x" << selection.height() << "+"
          << selection.x() << "+" << selection.y() << "\n";
    }

    if (tasks & CR::PRINT_RAW) {
        QByteArray byteArray;
        QBuffer buffer(&byteArray);
        capture.save(&buffer, "PNG");
        if (QFile file; file.open(stdout, QIODevice::WriteOnly)) {
            file.write(byteArray);
            file.close();
        }
    }

    if (tasks & CR::SAVE) {
        if (req.path().isEmpty()) {
            saveToFilesystemGUI(capture);
        } else {
            saveToFilesystem(capture, path);
        }
    }

    if (tasks & CR::COPY) {
        SnipSnapDaemon::copyToClipboard(capture);
    }

    if (tasks & CR::PIN) {
        SnipSnapDaemon::createPin(capture, selection);
        if (mode == CR::SCREEN_MODE || mode == CR::FULLSCREEN_MODE) {
            AbstractLogger::info()
              << QObject::tr("Full screen screenshot pinned to screen");
        }
    }

#ifdef ENABLE_IMGUR
    if (tasks & CR::UPLOAD) {
        if (!ConfigHandler().uploadWithoutConfirmation()) {
            auto* dialog = new ImgUploadDialog();
            if (dialog->exec() == QDialog::Rejected) {
                return;
            }
        }

        ImgUploaderBase* widget = ImgUploaderManager().uploader(capture);
        widget->show();
        widget->activateWindow();
        // NOTE: lambda can't capture 'this' because it might be destroyed later
        CR::ExportTask tasks = tasks;
        QObject::connect(
          widget, &ImgUploaderBase::uploadOk, [=, this](const QUrl& url) {
              if (ConfigHandler().copyURLAfterUpload()) {
                  if (!(tasks & CR::COPY)) {
                      SnipSnapDaemon::copyToClipboard(
                        url.toString(), tr("URL copied to clipboard."));
                  }
                  widget->showPostUploadDialog();
              }
          });
    }
#endif

    if (!(tasks & CR::UPLOAD)) {
        emit captureTaken(capture);
    }
}

void SnipSnap::setExternalWidget(bool b)
{
    m_haveExternalWidget = b;
}
bool SnipSnap::haveExternalWidget()
{
    return m_haveExternalWidget;
}

// STATIC ATTRIBUTES
SnipSnap::Origin SnipSnap::m_origin = SnipSnap::DAEMON;
