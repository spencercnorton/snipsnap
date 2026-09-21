
#include "screengrabber.h"
#include "core/qguiappcurrentscreen.h"
#include "utils/abstractlogger.h"
#include "utils/capturetrace.h"
#include "utils/confighandler.h"
#include "utils/monitorpreview.h"
#include "utils/systemnotification.h"

#include <QApplication>
#include <QEventLoop>
#include <QFile>
#include <QGuiApplication>
#include <QHBoxLayout>
#include <QImageReader>
#include <QKeyEvent>
#include <QLabel>
#include <QMouseEvent>
#include <QPainter>
#include <QPixmap>
#include <QProcess>
#include <QScreen>
#include <QTimer>
#include <QWidget>
#include <algorithm>

#ifdef SNIPSNAP_DEBUG_CAPTURE
#include <QDebug>
#endif

#if !(defined(Q_OS_MACOS) || defined(Q_OS_WIN))
#include "portalscreenshotrequest.h"
#include <QDir>
#include <QUrl>
#endif

bool ScreenGrabber::m_monitorSelectionActive = false;

ScreenGrabber::ScreenGrabber(QObject* parent)
  : QObject(parent)
  , m_selectedMonitor(-1)
  , m_monitorSelectionLoop(nullptr)
  , m_userCancelled(false)
{
    // Increase image allocation limit for large screenshots
    // (multi-monitor/high-DPI) Default is 128MB, set to 1GB to handle 4K+
    // multi-monitor setups
    QImageReader::setAllocationLimit(1024);
    const auto stopMonitorSelection = [this]() {
        if (!m_asyncCaptureBusy || !m_monitorSelectionLoop) {
            return;
        }
        m_selectedMonitor = -1;
        m_selectedScreen = nullptr;
        m_monitorSelectionLoop->quit();
    };
    connect(qApp,
            &QGuiApplication::screenAdded,
            this,
            [stopMonitorSelection](QScreen*) { stopMonitorSelection(); });
    connect(qApp,
            &QGuiApplication::screenRemoved,
            this,
            [stopMonitorSelection](QScreen*) { stopMonitorSelection(); });
}

ScreenGrabber::~ScreenGrabber()
{
#if defined(Q_OS_UNIX) && !defined(Q_OS_MACOS)
    delete m_portalParentDummy;
#endif
}

bool ScreenGrabber::grabEntireDesktopAsync(int preSelectedMonitor)
{
    return startAsync(AsyncMode::EntireDesktop, nullptr, preSelectedMonitor);
}

bool ScreenGrabber::grabFullDesktopAsync()
{
    return startAsync(AsyncMode::FullDesktop);
}

bool ScreenGrabber::grabScreenAsync(QScreen* screen)
{
    return startAsync(AsyncMode::Screen, screen);
}

bool ScreenGrabber::startAsync(AsyncMode mode,
                               QScreen* screen,
                               int preSelectedMonitor)
{
    if (m_asyncCaptureBusy) {
        emit captureRejected();
        return false;
    }
    if (mode == AsyncMode::Screen && !screen) {
        emit captureRejected();
        return false;
    }

    const QList<QScreen*> screens = QGuiApplication::screens();
    if ((mode == AsyncMode::Screen && !screens.contains(screen)) ||
        (preSelectedMonitor >= 0 && preSelectedMonitor >= screens.size())) {
        emit captureRejected();
        return false;
    }

    m_asyncCaptureBusy = true;
    CaptureTrace::mark(
      QStringLiteral("screen_grabber_started"),
      { { QStringLiteral("mode"), static_cast<int>(mode) },
        { QStringLiteral("monitor_count"), screens.size() } });
    m_asyncMode = mode;
    m_asyncScreen = mode == AsyncMode::Screen ? screen : nullptr;
    if (mode == AsyncMode::EntireDesktop && preSelectedMonitor >= 0) {
        m_asyncScreen = screens.at(preSelectedMonitor);
    }
    m_selectedScreen = nullptr;
    m_selectedMonitor = -1;
    m_asyncScreensSnapshot.clear();
    m_asyncScreenGeometries.clear();
    m_asyncScreenDevicePixelRatios.clear();
    for (QScreen* current : screens) {
        m_asyncScreensSnapshot.append(current);
        m_asyncScreenGeometries.append(current->geometry());
        m_asyncScreenDevicePixelRatios.append(current->devicePixelRatio());
    }
    if (mode == AsyncMode::Screen) {
        m_selectedScreen = screen;
        m_selectedMonitor = screens.indexOf(screen);
    }

    QTimer::singleShot(0, this, [this]() {
#if defined(Q_OS_UNIX) && !defined(Q_OS_MACOS)
        if (m_info.waylandDetected() ||
            !ConfigHandler().useX11LegacyScreenshot()) {
            startPortalCapture();
            return;
        }
#endif
        performAsyncNativeCapture();
    });
    return true;
}

void ScreenGrabber::performAsyncNativeCapture()
{
    bool ok = false;
    QPixmap screenshot;
    if (m_asyncMode != AsyncMode::FullDesktop && !screenLayoutUnchanged()) {
        completeAsync(QPixmap(), false, false);
        return;
    }
    switch (m_asyncMode) {
        case AsyncMode::EntireDesktop:
#if defined(Q_OS_MACOS)
            m_selectedScreen = QGuiAppCurrentScreen().currentScreen();
            m_selectedMonitor =
              QGuiApplication::screens().indexOf(m_selectedScreen);
#endif
            screenshot = grabEntireDesktop(
              ok, QGuiApplication::screens().indexOf(m_asyncScreen));
            break;
        case AsyncMode::FullDesktop:
            screenshot = grabFullDesktop(ok);
            break;
        case AsyncMode::Screen:
            if (!m_asyncScreen) {
                ok = false;
                break;
            }
            screenshot = grabScreen(m_asyncScreen, ok);
            break;
    }
    completeAsync(screenshot, ok, false);
}

#if defined(Q_OS_UNIX) && !defined(Q_OS_MACOS)
void ScreenGrabber::startPortalCapture()
{
    CaptureTrace::mark(QStringLiteral("portal_capture_started"));
    m_portalRequest = new PortalScreenshotRequest(this);
    connect(m_portalRequest,
            &PortalScreenshotRequest::finished,
            this,
            [this](PortalScreenshotRequest::Result result,
                   const QUrl& uri,
                   const QString& errorMessage) {
                CaptureTrace::mark(
                  QStringLiteral("portal_capture_finished"),
                  { { QStringLiteral("result"),
                      static_cast<int>(result) } });
                if (m_portalParentDummy) {
                    m_portalParentDummy->deleteLater();
                    m_portalParentDummy = nullptr;
                }
                m_portalRequest->deleteLater();
                m_portalRequest = nullptr;

                if (result != PortalScreenshotRequest::Result::Success) {
                    if (result == PortalScreenshotRequest::Result::Cancelled) {
                        AbstractLogger::info() << tr("Screenshot cancelled");
                    } else {
                        AbstractLogger::error() << errorMessage;
                    }
                    completeAsync(QPixmap(), false);
                    return;
                }
                const QString path = uri.toLocalFile();
                CaptureTrace::mark(
                  QStringLiteral("image_decode_started"));
                QPixmap screenshot(path);
                CaptureTrace::mark(
                  QStringLiteral("image_decode_finished"),
                  { { QStringLiteral("ok"), !screenshot.isNull() },
                    { QStringLiteral("width"), screenshot.width() },
                    { QStringLiteral("height"), screenshot.height() } });
                QFile::remove(path);
                if (screenshot.isNull()) {
                    AbstractLogger::error()
                      << tr("Unable to load the screenshot returned by the "
                            "portal");
                    completeAsync(QPixmap(), false);
                    return;
                }
                completeAsync(screenshot, true);
            });

    QString parentWindow;
    if (QGuiApplication::platformName() != QLatin1String("wayland")) {
        // The native X11 handle must remain alive for the whole request.
        m_portalParentDummy = new QWidget;
        m_portalParentDummy->setAttribute(Qt::WA_DontShowOnScreen, true);
        m_portalParentDummy->resize(1, 1);
        m_portalParentDummy->show();
        parentWindow =
          QStringLiteral("x11:0x%1").arg(m_portalParentDummy->winId(), 0, 16);
    }

    bool timeoutConfigured = false;
    int timeoutMs = qEnvironmentVariableIntValue("SNIPSNAP_PORTAL_TIMEOUT_MS",
                                                 &timeoutConfigured);
    if (!timeoutConfigured) {
        // A portal may show a consent prompt before returning the complete
        // desktop image. Give a human enough time to answer it; the request
        // remains bounded and can still be overridden for automated tests.
        timeoutMs = 300000;
    }
    timeoutMs = qBound(1000, timeoutMs, 600000);
    // Keep the portal capture non-interactive: SnipSnap needs the full image
    // and its monitor geometry so its own cross-desktop selection/editor can
    // operate correctly. Portal v3's interactive mode returns only a cropped
    // image and no source geometry, which cannot be mapped back onto a
    // multi-monitor canvas without guessing.
    m_portalRequest->start(parentWindow, timeoutMs, false);
}
#endif

void ScreenGrabber::completeAsync(const QPixmap& rawScreenshot,
                                  bool ok,
                                  bool processPortalResult)
{
    QPixmap screenshot = rawScreenshot;
    if (ok && m_asyncMode != AsyncMode::FullDesktop &&
        !screenLayoutUnchanged()) {
        ok = false;
        screenshot = QPixmap();
    }
    if (ok && processPortalResult) {
        if (m_asyncMode == AsyncMode::Screen) {
            if (!ok || !m_asyncScreen) {
                ok = false;
            } else {
                m_selectedScreen = m_asyncScreen;
                m_selectedMonitor =
                  QGuiApplication::screens().indexOf(m_selectedScreen);
                screenshot = cropToMonitor(screenshot, m_selectedScreen, ok);
            }
        } else if (m_asyncMode == AsyncMode::EntireDesktop) {
            if (!ok) {
                screenshot = QPixmap();
            } else if (m_asyncScreen) {
                m_selectedScreen = m_asyncScreen;
                m_selectedMonitor =
                  QGuiApplication::screens().indexOf(m_selectedScreen);
                screenshot = cropToMonitor(screenshot, m_selectedScreen, ok);
            } else {
                screenshot = selectMonitorAndCrop(screenshot, ok);
            }
        }
    }
    if (!ok) {
        // Never expose a full-desktop payload on any failed or cancelled
        // capture, even to callers that also receive the false status flag.
        screenshot = QPixmap();
    }

    m_asyncCaptureBusy = false;
    m_asyncScreen = nullptr;
    m_asyncScreensSnapshot.clear();
    m_asyncScreenGeometries.clear();
    m_asyncScreenDevicePixelRatios.clear();
    CaptureTrace::mark(
      QStringLiteral("screen_grabber_completed"),
      { { QStringLiteral("ok"), ok && !screenshot.isNull() },
        { QStringLiteral("selected_monitor"), m_selectedMonitor },
        { QStringLiteral("width"), screenshot.width() },
        { QStringLiteral("height"), screenshot.height() } });
    emit captureFinished(screenshot, ok && !screenshot.isNull());
}

bool ScreenGrabber::screenLayoutUnchanged() const
{
    if (!m_asyncCaptureBusy) {
        return true;
    }
    const QList<QScreen*> currentScreens = QGuiApplication::screens();
    if (currentScreens.size() != m_asyncScreensSnapshot.size()) {
        return false;
    }
    for (qsizetype i = 0; i < m_asyncScreensSnapshot.size(); ++i) {
        QScreen* screen = m_asyncScreensSnapshot.at(i);
        if (!screen || !currentScreens.contains(screen) ||
            screen->geometry() != m_asyncScreenGeometries.at(i) ||
            !qFuzzyCompare(screen->devicePixelRatio(),
                           m_asyncScreenDevicePixelRatios.at(i))) {
            return false;
        }
    }
    return true;
}

QPixmap ScreenGrabber::selectMonitorAndCrop(const QPixmap& fullScreenshot,
                                            bool& ok)
{
    ok = true;
#if defined(Q_OS_MACOS)
    // Avoid showing additional top-level monitor selection UI on macOS
    // Only screenshot the monitor where the tray activated the screenshot
    QScreen* screen = QGuiApplication::screens().value(0);
    m_selectedScreen = screen;
    m_selectedMonitor = QGuiApplication::screens().indexOf(screen);
    return cropToMonitor(fullScreenshot, screen, ok);
#else
    // If there's only one monitor, skip selection
    const QList<QScreen*> screens = QGuiApplication::screens();
    if (screens.size() == 1) {
        m_selectedScreen = screens.first();
        m_selectedMonitor = 0;
        return cropToMonitor(fullScreenshot, m_selectedScreen, ok);
    }

    // Capture Active Monitor: auto-select monitor under cursor
    if (ConfigHandler().captureActiveMonitor()) {
        if (m_info.waylandDetected()) {
            AbstractLogger::error()
              << tr("Capture Active Monitor is not supported on Wayland due to "
                    "Wayland security model.");
            ok = false;
            return QPixmap();
        }

        QGuiAppCurrentScreen screenFinder;
        QScreen* cursorScreen = screenFinder.currentScreen();
        int monitorIndex = screens.indexOf(cursorScreen);
        if (monitorIndex >= 0) {
            m_selectedMonitor = monitorIndex;
            m_selectedScreen = cursorScreen;
            return cropToMonitor(fullScreenshot, cursorScreen, ok);
        }
        // Fall through to manual selection if screen lookup fails
    }

    if (m_monitorSelectionActive) {
        AbstractLogger::error()
          << tr("Screenshot already in progress, please wait for the current "
                "screenshot to complete");
        ok = false;
        return QPixmap();
    }

    m_monitorSelectionActive = true;
    m_selectedMonitor = -1;
    m_userCancelled = false;
    CaptureTrace::mark(
      QStringLiteral("monitor_picker_construction_started"),
      { { QStringLiteral("monitor_count"), screens.size() } });
    QWidget* container = createMonitorPreviews(fullScreenshot);
    if (!container) {
        m_monitorSelectionActive = false;
        CaptureTrace::mark(
          QStringLiteral("monitor_picker_construction_failed"));
        ok = false;
        return QPixmap();
    }
    CaptureTrace::mark(QStringLiteral("monitor_picker_shown"));

    // Wait for the separate monitor-picker UI. Portal transport is already
    // complete and does not use this legacy modal loop; converting the picker
    // itself to signal-driven completion is tracked as downstream follow-up.
    QEventLoop loop;
    m_monitorSelectionLoop = &loop;
    loop.exec();
    m_monitorSelectionLoop = nullptr;

    CaptureTrace::mark(
      QStringLiteral("monitor_picker_finished"),
      { { QStringLiteral("selected_monitor"), m_selectedMonitor },
        { QStringLiteral("cancelled"), m_userCancelled } });

    delete container;
    m_monitorSelectionActive = false;

    if (m_selectedMonitor >= 0) {
        return cropToMonitor(fullScreenshot, m_selectedScreen, ok);
    } else {
        ok = false;
        if (m_userCancelled) {
            AbstractLogger::info() << tr("Screenshot cancelled");
        }
        return QPixmap();
    }
#endif
}

QPixmap ScreenGrabber::grabEntireDesktop(bool& ok, int preSelectedMonitor)
{
    ok = true;
    QPixmap screenshot;

#if defined(Q_OS_MACOS)
    constexpr int wid = 0;
    QScreen* currentScreen = QGuiAppCurrentScreen().currentScreen();
    if (!currentScreen) {
        AbstractLogger::error() << tr("Unable to get current screen");
        ok = false;
        return QPixmap();
    }
    const QRect geom = currentScreen->geometry();
    screenshot = currentScreen->grabWindow(
      wid, geom.x(), geom.y(), geom.width(), geom.height());
    screenshot.setDevicePixelRatio(currentScreen->devicePixelRatio());
    return screenshot;

#elif defined(Q_OS_UNIX) && !defined(Q_OS_MACOS)
    if (!m_info.waylandDetected() && ConfigHandler().useX11LegacyScreenshot()) {
        screenshot = x11LegacyScreenshot();
        ok = !screenshot.isNull();
        if (!ok) {
            AbstractLogger::error() << tr("Unable to capture screen");
            return QPixmap();
        }
    } else {
        ok = false;
        AbstractLogger::error()
          << tr("Portal screenshots require the asynchronous capture API");
        return QPixmap();
    }
#elif defined(Q_OS_WIN)
    screenshot = windowsScreenshot(0);
#endif

    // If monitor was pre-selected skip UI and crop directly
    if (preSelectedMonitor >= 0) {
        const QList<QScreen*> screens = QGuiApplication::screens();
        if (preSelectedMonitor < screens.size()) {
            m_selectedMonitor = preSelectedMonitor;
            m_selectedScreen = screens.at(preSelectedMonitor);
            return cropToMonitor(screenshot, m_selectedScreen, ok);
        }
        ok = false;
        return QPixmap();
    }

    return selectMonitorAndCrop(screenshot, ok);
}

QPixmap ScreenGrabber::grabFullDesktop(bool& ok)
{
    ok = true;
    QPixmap screenshot;

#if defined(Q_OS_MACOS)
    // On macOS, composite all screens into a single pixmap.
    const QList<QScreen*> screens = QGuiApplication::screens();
    QRect totalGeom;
    for (QScreen* s : screens) {
        totalGeom = totalGeom.united(s->geometry());
    }
    qreal maxDpr = 1.0;
    for (QScreen* s : screens) {
        maxDpr = qMax(maxDpr, s->devicePixelRatio());
    }
    screenshot = QPixmap(qRound(totalGeom.width() * maxDpr),
                         qRound(totalGeom.height() * maxDpr));
    screenshot.setDevicePixelRatio(maxDpr);
    screenshot.fill(Qt::black);
    QPainter painter(&screenshot);
    for (QScreen* s : screens) {
        QRect geom = s->geometry();
        QPixmap p = s->grabWindow(0);
        QPoint offset = geom.topLeft() - totalGeom.topLeft();
        painter.drawPixmap(offset, p);
    }
    painter.end();
#elif defined(Q_OS_UNIX) && !defined(Q_OS_MACOS)
    if (!m_info.waylandDetected() && ConfigHandler().useX11LegacyScreenshot()) {
        screenshot = x11LegacyScreenshot();
        ok = !screenshot.isNull();
        if (!ok) {
            AbstractLogger::error() << tr("Unable to capture screen");
        }
    } else {
        ok = false;
        AbstractLogger::error()
          << tr("Portal screenshots require the asynchronous capture API");
    }
#elif defined(Q_OS_WIN)
    screenshot = windowsScreenshot(0);
#endif

    return screenshot;
}

QRect ScreenGrabber::screenGeometry(QScreen* screen)
{
    QRect geometry = screen->geometry();
    if (m_info.waylandDetected()) {
        QPoint topLeft(0, 0);
        geometry.moveTo(geometry.topLeft() - topLeft);
    }
    return geometry;
}

QPixmap ScreenGrabber::grabScreen(QScreen* screen, bool& ok)
{
    QPixmap p;
#if defined(Q_OS_UNIX) && !defined(Q_OS_MACOS)
    const QList<QScreen*> screens = QGuiApplication::screens();
    int screenIndex = screens.indexOf(screen);

    p = grabEntireDesktop(ok, screenIndex);
#else
    const QRect geometry = screenGeometry(screen);
    ok = true;
    return screen->grabWindow(
      0, geometry.x(), geometry.y(), geometry.width(), geometry.height());
#endif
    return p;
}

QRect ScreenGrabber::desktopGeometry()
{
    QRect geometry;

    for (QScreen* const screen : QGuiApplication::screens()) {
        QRect scrRect = screen->geometry();
#if !defined(Q_OS_WIN)
        // https://doc.qt.io/qt-6/highdpi.html#device-independent-screen-geometry
        qreal dpr = screen->devicePixelRatio();
        scrRect.moveTo(QPointF(scrRect.x() / dpr, scrRect.y() / dpr).toPoint());
#endif
        geometry = geometry.united(scrRect);
    }
    return geometry;
}

QScreen* ScreenGrabber::getSelectedScreen() const
{
    return m_selectedScreen;
}

QWidget* ScreenGrabber::createMonitorPreviews(const QPixmap& fullScreenshot)
{
    const QList<QScreen*> screens = QGuiApplication::screens();

#ifdef SNIPSNAP_DEBUG_CAPTURE
    qDebug() << tr("=== All Screen Information ===");
    for (int i = 0; i < screens.size(); ++i) {
        QScreen* s = screens[i];
        qDebug() << tr("Screen %1: %2").arg(i).arg(s->name());
        qDebug() << tr("  Logical geometry: %1x%2+%3+%4")
                      .arg(s->geometry().width())
                      .arg(s->geometry().height())
                      .arg(s->geometry().x())
                      .arg(s->geometry().y());
        qDebug() << tr("  DPR: %1").arg(s->devicePixelRatio());
    }
#endif

    QWidget* monitorPreviews = new QWidget(
      nullptr, Qt::Window | Qt::FramelessWindowHint | Qt::WindowStaysOnTopHint);
    monitorPreviews->setAttribute(Qt::WA_TranslucentBackground);
    monitorPreviews->setStyleSheet(
      "QWidget { background-color: transparent; }");
    monitorPreviews->installEventFilter(this); // For ESC key handling
    monitorPreviews->setFocusPolicy(Qt::StrongFocus);

    QHBoxLayout* containerLayout = new QHBoxLayout(monitorPreviews);
    containerLayout->setSpacing(20);
    containerLayout->setContentsMargins(20, 20, 20, 20);

    // Build list of screen indices sorted by X position (left to right)
    QList<int> sortedIndices;
    for (int i = 0; i < screens.size(); ++i) {
        sortedIndices.append(i);
    }
    std::sort(
      sortedIndices.begin(), sortedIndices.end(), [&screens](int a, int b) {
          return screens[a]->geometry().x() < screens[b]->geometry().x();
      });

    for (int i : sortedIndices) {
        QScreen* screen = screens[i];

        bool cropOk = false;
        QPixmap cropped = cropToMonitor(fullScreenshot, screen, cropOk);
        if (!cropOk) {
            continue;
        }
        QPixmap thumbnail = cropped.scaled(
          400, 250, Qt::KeepAspectRatio, Qt::SmoothTransformation);
        thumbnail.setDevicePixelRatio(1.0);

        MonitorPreview* preview =
          new MonitorPreview(i, screen, thumbnail, monitorPreviews);

        QPointer<QScreen> selectedScreen = screen;
        connect(preview,
                &MonitorPreview::monitorSelected,
                this,
                [this, selectedScreen](int) {
                    const QList<QScreen*> currentScreens =
                      QGuiApplication::screens();
                    if (selectedScreen && screenLayoutUnchanged()) {
                        m_selectedScreen = selectedScreen;
                        m_selectedMonitor =
                          currentScreens.indexOf(selectedScreen);
                    } else {
                        m_selectedScreen = nullptr;
                        m_selectedMonitor = -1;
                    }
                    if (m_monitorSelectionLoop) {
                        m_monitorSelectionLoop->quit();
                    }
                });

        containerLayout->addWidget(preview);
    }

    if (containerLayout->count() == 0) {
        delete monitorPreviews;
        return nullptr;
    }

    monitorPreviews->setLayout(containerLayout);
    monitorPreviews->adjustSize();

    QScreen* primaryScreen = QGuiApplication::primaryScreen();
    if (!primaryScreen) {
        delete monitorPreviews;
        return nullptr;
    }
    QRect screenGeometry = primaryScreen->geometry();
    QPoint center = screenGeometry.center();
    monitorPreviews->move(center.x() - monitorPreviews->width() / 2,
                          center.y() - monitorPreviews->height() / 2);

    monitorPreviews->show();
    return monitorPreviews;
}

bool ScreenGrabber::eventFilter(QObject* obj, QEvent* event)
{
    if (event->type() == QEvent::Close && m_monitorSelectionLoop) {
        // A window-manager close (for example Alt+F4) must release the nested
        // picker loop just like Escape; otherwise the global capture
        // reservation remains stuck until SnipSnap restarts.
        m_selectedMonitor = -1;
        m_selectedScreen = nullptr;
        m_userCancelled = true;
        m_monitorSelectionLoop->quit();
    }
    if (event->type() == QEvent::KeyPress) {
        QKeyEvent* keyEvent = static_cast<QKeyEvent*>(event);
        if (keyEvent->key() == Qt::Key_Escape) {
            // User cancelled selection
            m_selectedMonitor = -1;
            m_userCancelled = true;
            if (m_monitorSelectionLoop) {
                m_monitorSelectionLoop->quit();
            }
            return true;
        }
    }
    return QObject::eventFilter(obj, event);
}

QPixmap ScreenGrabber::cropToMonitor(const QPixmap& fullScreenshot,
                                     QScreen* targetScreen,
                                     bool& ok)
{
    ok = false;
    const QList<QScreen*> screens = QGuiApplication::screens();
    if (!targetScreen || !screens.contains(targetScreen) ||
        !screenLayoutUnchanged()) {
        return QPixmap();
    }

    QRect targetGeometry = targetScreen->geometry();
    qreal targetDpr = targetScreen->devicePixelRatio();

    // Calculate total logical dimensions and minimum coordinates
    int minX = INT_MAX, minY = INT_MAX;
    int maxX = INT_MIN, maxY = INT_MIN;

    for (QScreen* screen : screens) {
        QRect geo = screen->geometry();
        minX = qMin(minX, geo.x());
        minY = qMin(minY, geo.y());
        maxX = qMax(maxX, geo.x() + geo.width());
        maxY = qMax(maxY, geo.y() + geo.height());
    }

    int totalLogicalWidth = maxX - minX;
    int totalLogicalHeight = maxY - minY;

#ifdef SNIPSNAP_DEBUG_CAPTURE
    qDebug() << tr("Total logical dimensions: %1x%2 (min: %3,%4)")
                  .arg(totalLogicalWidth)
                  .arg(totalLogicalHeight)
                  .arg(minX)
                  .arg(minY);
    qDebug() << tr("Screenshot dimensions: %1x%2")
                  .arg(fullScreenshot.width())
                  .arg(fullScreenshot.height());
#endif

    int cropX, cropY, cropWidth, cropHeight;

#if defined(Q_OS_UNIX) && !defined(Q_OS_MACOS)
    // Linux (both X11 and Wayland via freedesktop portal):
    // Use logical coordinate-based cropping since portal returns full
    // desktop
    qreal screenshotScaleX = (qreal)fullScreenshot.width() / totalLogicalWidth;
    qreal screenshotScaleY =
      (qreal)fullScreenshot.height() / totalLogicalHeight;

#ifdef SNIPSNAP_DEBUG_CAPTURE
    qDebug() << tr("Screenshot scale factors: X=%1 Y=%2")
                  .arg(screenshotScaleX)
                  .arg(screenshotScaleY);
#endif

    cropX = qRound((targetGeometry.x() - minX) * screenshotScaleX);
    cropY = qRound((targetGeometry.y() - minY) * screenshotScaleY);
    cropWidth = qRound(targetGeometry.width() * screenshotScaleX);
    cropHeight = qRound(targetGeometry.height() * screenshotScaleY);
#else
    // Windows: Calculate physical pixel positions for mixed DPI
    cropX = 0;
    cropY = 0;

    for (QScreen* screen : screens) {
        QRect geom = screen->geometry();
        qreal dpr = screen->devicePixelRatio();

        // Sum physical widths of screens completely to the left
        if (geom.x() + geom.width() <= targetGeometry.x()) {
            cropX += qRound(geom.width() * dpr);
        }

        // Sum physical heights of screens completely above
        if (geom.y() + geom.height() <= targetGeometry.y()) {
            cropY += qRound(geom.height() * dpr);
        }
    }

    cropWidth = qRound(targetGeometry.width() * targetDpr);
    cropHeight = qRound(targetGeometry.height() * targetDpr);

#ifdef SNIPSNAP_DEBUG_CAPTURE
    qDebug() << tr("Calculated crop position for mixed DPI: X=%1 Y=%2")
                  .arg(cropX)
                  .arg(cropY);
#endif
#endif

    QRect cropRect(cropX, cropY, cropWidth, cropHeight);

#ifdef SNIPSNAP_DEBUG_CAPTURE
    qDebug() << tr("Screen %1: %2")
                  .arg(screens.indexOf(targetScreen))
                  .arg(targetScreen->name());
    qDebug() << tr("  Logical geometry: %1x%2+%3+%4 DPR: %5")
                  .arg(targetGeometry.width())
                  .arg(targetGeometry.height())
                  .arg(targetGeometry.x())
                  .arg(targetGeometry.y())
                  .arg(targetDpr);
    qDebug() << tr("  Crop rect in screenshot: %1x%2+%3+%4")
                  .arg(cropRect.width())
                  .arg(cropRect.height())
                  .arg(cropRect.x())
                  .arg(cropRect.y());
#endif

    // Ensure crop rect is within bounds
    cropRect = cropRect.intersected(
      QRect(0, 0, fullScreenshot.width(), fullScreenshot.height()));

    if (cropRect.isEmpty()) {
        AbstractLogger::warning() << tr("Unable to crop the selected monitor");
        return QPixmap();
    }

    QPixmap cropped = fullScreenshot.copy(cropRect);

#if defined(Q_OS_UNIX) && !defined(Q_OS_MACOS)
    // Linux: May need rescaling if scale factors don't match
    if (qAbs(screenshotScaleX - targetDpr) > 0.01) {
        int targetPhysicalWidth = qRound(targetGeometry.width() * targetDpr);
        int targetPhysicalHeight = qRound(targetGeometry.height() * targetDpr);
        cropped = cropped.scaled(targetPhysicalWidth,
                                 targetPhysicalHeight,
                                 Qt::IgnoreAspectRatio,
                                 Qt::SmoothTransformation);
#ifdef SNIPSNAP_DEBUG_CAPTURE
        qDebug() << tr("Scaling screenshot to: %1 %2")
                      .arg(targetPhysicalWidth)
                      .arg(targetPhysicalHeight);
#endif
    }
#endif
    // Cropped region should be at target monitor's native DPR
    cropped.setDevicePixelRatio(targetDpr);

    ok = !cropped.isNull();
    return cropped;
}

QPixmap ScreenGrabber::windowsScreenshot(int wid)
{
    const QList<QScreen*> screens = QGuiApplication::screens();

    int canvasWidth = 0;
    int canvasHeight = 0;

    // Build a map tracking where each screen should be positioned in
    // physical pixels
    struct ScreenInfo
    {
        QRect physicalRect; // Where to draw in the canvas
        QPixmap pixmap;
    };
    QMap<QScreen*, ScreenInfo> screenInfos;

    for (QScreen* screen : screens) {
        QRect screenGeom = screen->geometry();

        QPixmap screenPixmap = screen->grabWindow(wid);
        screenPixmap.setDevicePixelRatio(1.0);

        int physicalWidth = screenPixmap.width();
        int physicalHeight = screenPixmap.height();

        int physicalX = 0;
        int physicalY = 0;

        for (QScreen* otherScreen : screens) {
            QRect otherGeom = otherScreen->geometry();
            qreal otherDpr = otherScreen->devicePixelRatio();

            // If this screen is entirely to the left of current screen
            if (otherGeom.x() + otherGeom.width() <= screenGeom.x()) {
                physicalX += qRound(otherGeom.width() * otherDpr);
            }

            // If this screen is entirely above the current screen
            if (otherGeom.y() + otherGeom.height() <= screenGeom.y()) {
                physicalY += qRound(otherGeom.height() * otherDpr);
            }
        }

        ScreenInfo info;
        info.physicalRect =
          QRect(physicalX, physicalY, physicalWidth, physicalHeight);
        info.pixmap = screenPixmap;
        screenInfos[screen] = info;

        canvasWidth = qMax(canvasWidth, physicalX + physicalWidth);
        canvasHeight = qMax(canvasHeight, physicalY + physicalHeight);
    }

    // Composite all screens onto canvas
    QPixmap desktop(canvasWidth, canvasHeight);
    desktop.fill(Qt::black);

    QPainter painter(&desktop);
    painter.setCompositionMode(QPainter::CompositionMode_Source);

    for (QScreen* screen : screens) {
        const ScreenInfo& info = screenInfos[screen];
        painter.drawPixmap(info.physicalRect.topLeft(), info.pixmap);
    }
    painter.end();

    return desktop;
}

QPixmap ScreenGrabber::x11LegacyScreenshot()
{
    const QList<QScreen*> screens = QGuiApplication::screens();

    if (screens.isEmpty()) {
        return QPixmap();
    }

    if (screens.size() == 1) {
        QScreen* screen = screens.first();
        QPixmap p = screen->grabWindow(0);
        p.setDevicePixelRatio(screen->devicePixelRatio());
        return p;
    }

    // Composite all screens using logical geometry.
    // On i3 (tested) DPR is uniform so we don't need the per-screen
    // physical pixel math that the Windows backend does. Not sure if this is
    // true for other DE's like xmonad.
    QRect totalGeom;
    for (QScreen* s : screens) {
        totalGeom = totalGeom.united(s->geometry());
    }

    qreal dpr = screens.first()->devicePixelRatio();
    QPixmap desktop(qRound(totalGeom.width() * dpr),
                    qRound(totalGeom.height() * dpr));
    desktop.setDevicePixelRatio(dpr);
    desktop.fill(Qt::black);

    QPainter painter(&desktop);
    for (QScreen* s : screens) {
        QPixmap p = s->grabWindow(0);
        QPoint offset = s->geometry().topLeft() - totalGeom.topLeft();
        painter.drawPixmap(offset, p);
    }
    painter.end();

    return desktop;
}
