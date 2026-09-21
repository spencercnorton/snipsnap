// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2017-2019 Alejandro Sirgo Rica & Contributors

#pragma once

#include "utils/desktopinfo.h"

#include <QEvent>
#include <QList>
#include <QObject>
#include <QPixmap>
#include <QPointer>
#include <QScreen>

class QEventLoop;
class QWidget;
#if defined(Q_OS_UNIX) && !defined(Q_OS_MACOS)
class PortalScreenshotRequest;
#endif

class ScreenGrabber : public QObject
{
    Q_OBJECT
public:
    explicit ScreenGrabber(QObject* parent = nullptr);
    ~ScreenGrabber() override;
    bool grabEntireDesktopAsync(int preSelectedMonitor = -1);
    bool grabFullDesktopAsync();
    bool grabScreenAsync(QScreen* screen);
    QPixmap grabEntireDesktop(bool& ok, int preSelectedMonitor = -1);
    QPixmap grabFullDesktop(bool& ok);
    QRect screenGeometry(QScreen* screen);
    QPixmap grabScreen(QScreen* screenNumber, bool& ok);
    QRect desktopGeometry();
    QRect logicalDesktopGeometry();
    int getSelectedMonitor() const { return m_selectedMonitor; }
    QScreen* getSelectedScreen() const;
    QPixmap selectMonitorAndCrop(const QPixmap& fullScreenshot, bool& ok);

signals:
    void captureFinished(const QPixmap& screenshot, bool ok);
    void captureRejected();

protected:
    bool eventFilter(QObject* obj, QEvent* event) override;

private:
    enum class AsyncMode
    {
        EntireDesktop,
        FullDesktop,
        Screen
    };

    bool startAsync(AsyncMode mode,
                    QScreen* screen = nullptr,
                    int preSelectedMonitor = -1);
    void performAsyncNativeCapture();
    void completeAsync(const QPixmap& screenshot,
                       bool ok,
                       bool processPortalResult = true);
    bool screenLayoutUnchanged() const;
#if defined(Q_OS_UNIX) && !defined(Q_OS_MACOS)
    void startPortalCapture();
#endif
    void adjustDevicePixelRatio(QPixmap& pixmap);
    QWidget* createMonitorPreviews(const QPixmap& fullScreenshot);
    QPixmap cropToMonitor(const QPixmap& fullScreenshot,
                          QScreen* targetScreen,
                          bool& ok);
    QPixmap windowsScreenshot(int wid);
    QPixmap x11LegacyScreenshot();

    DesktopInfo m_info;
    QPixmap Screenshot;
    int m_selectedMonitor;
    QPointer<QScreen> m_selectedScreen;
    QEventLoop* m_monitorSelectionLoop;
    bool m_userCancelled;
    bool m_asyncCaptureBusy{ false };
    AsyncMode m_asyncMode{ AsyncMode::EntireDesktop };
    QPointer<QScreen> m_asyncScreen;
    QList<QPointer<QScreen>> m_asyncScreensSnapshot;
    QList<QRect> m_asyncScreenGeometries;
    QList<qreal> m_asyncScreenDevicePixelRatios;
#if defined(Q_OS_UNIX) && !defined(Q_OS_MACOS)
    PortalScreenshotRequest* m_portalRequest{ nullptr };
    QWidget* m_portalParentDummy{ nullptr };
#endif
    static bool m_monitorSelectionActive;
};
