// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-FileCopyrightText: 2017-2019 Alejandro Sirgo Rica & Contributors

#ifdef USE_KDSINGLEAPPLICATION
#include <kdsingleapplication.h>
#ifdef Q_OS_UNIX
#include "core/signaldaemon.h"
#include <csignal>
#endif
#endif

#include "cli/commandlineparser.h"
#include "config/cacheutils.h"
#include "config/styleoverride.h"
#include "core/capturerequest.h"
#include "core/snipsnap.h"
#include "core/snipsnapdaemon.h"
#include "utils/abstractlogger.h"
#include "utils/capturetrace.h"
#include "utils/confighandler.h"
#include "utils/filenamehandler.h"
#include "utils/pathinfo.h"
#include "utils/valuehandler.h"

#if !(defined(Q_OS_MACOS) || defined(Q_OS_WIN))
#include "core/snipsnapdbusadapter.h"
#include <QDBusConnection>
#include <QDBusMessage>
#endif

#include <QApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QLibraryInfo>
#include <QNetworkProxyFactory>
#include <QSharedMemory>
#include <QStandardPaths>
#include <QTimer>
#include <QTranslator>

// Required for saving button list QList<CaptureTool::Type>
Q_DECLARE_METATYPE(QList<int>)

#if defined(USE_KDSINGLEAPPLICATION) && defined(Q_OS_UNIX)
class UnixSignalHandlers
{
public:
    UnixSignalHandlers() = default;
    UnixSignalHandlers(const UnixSignalHandlers&) = delete;
    UnixSignalHandlers& operator=(const UnixSignalHandlers&) = delete;

    ~UnixSignalHandlers()
    {
        if (m_termInstalled) {
            ::sigaction(SIGTERM, &m_previousTerm, nullptr);
        }
        if (m_intInstalled) {
            ::sigaction(SIGINT, &m_previousInt, nullptr);
        }
    }

    int install()
    {
        struct sigaction interruptAction{};
        interruptAction.sa_handler = SignalDaemon::intSignalHandler;
        ::sigemptyset(&interruptAction.sa_mask);
        interruptAction.sa_flags = SA_RESTART;

        if (::sigaction(SIGINT, &interruptAction, &m_previousInt) == -1) {
            return 1;
        }
        m_intInstalled = true;

        struct sigaction terminateAction{};
        terminateAction.sa_handler = SignalDaemon::termSignalHandler;
        ::sigemptyset(&terminateAction.sa_mask);
        terminateAction.sa_flags = SA_RESTART;

        if (::sigaction(SIGTERM, &terminateAction, &m_previousTerm) == -1) {
            ::sigaction(SIGINT, &m_previousInt, nullptr);
            m_intInstalled = false;
            return 2;
        }
        m_termInstalled = true;
        return 0;
    }

private:
    struct sigaction m_previousInt{};
    struct sigaction m_previousTerm{};
    bool m_intInstalled = false;
    bool m_termInstalled = false;
};
#endif

int requestCaptureAndWait(const CaptureRequest& req)
{
    const CaptureTrace::TimelineStart traceStart =
      req.captureMode() == CaptureRequest::GRAPHICAL_MODE
        ? CaptureTrace::processStart()
        : CaptureTrace::TimelineStart{};
    const bool traceStarted =
      req.captureMode() == CaptureRequest::GRAPHICAL_MODE &&
      CaptureTrace::begin(QStringLiteral("cli_graphical"), traceStart);
    SnipSnap* snipsnap = SnipSnap::instance();
    if (!snipsnap->requestCapture(req)) {
        if (traceStarted) {
            CaptureTrace::finish(QStringLiteral("capture_rejected"),
                                 { { QStringLiteral("reason"),
                                     QStringLiteral("request_rejected") } });
        }
        return E_ABORTED;
    }
    QObject::connect(snipsnap, &SnipSnap::captureTaken, [&](const QPixmap&) {
#if defined(Q_OS_MACOS)
        // Only useful on MacOS because each instance hosts its own widgets
        if (!SnipSnapDaemon::isThisInstanceHostingWidgets()) {
            QCoreApplication::exit(0);
        }
#else
        // if this instance is not daemon, make sure it exit after caputre finish
        if (SnipSnapDaemon::instance() == nullptr &&
            !SnipSnap::instance()->haveExternalWidget()) {
            QCoreApplication::exit(E_OK);
        }
#endif
    });
    QObject::connect(snipsnap, &SnipSnap::captureFailed, []() {
        AbstractLogger::Target logTarget = static_cast<AbstractLogger::Target>(
          ConfigHandler().showAbortNotification()
            ? AbstractLogger::Target::Default
            : AbstractLogger::Target::Default &
                ~AbstractLogger::Target::Notification);
        AbstractLogger::info(logTarget) << "Screenshot aborted.";
        QCoreApplication::exit(E_ABORTED);
    });
    return QCoreApplication::exec();
}

QSharedMemory* guiMutexLock()
{
    QString key = "tech.norvi.snipsnap-" APP_VERSION;
    auto* shm = new QSharedMemory(key);
#ifdef Q_OS_UNIX
    // Destroy shared memory if the last instance crashed on Unix
    shm->attach();
    delete shm;
    shm = new QSharedMemory(key);
#endif
    if (!shm->create(1)) {
        delete shm;
        return nullptr;
    }
    return shm;
}

void configureTranslation(QTranslator& translator, QTranslator& qtTranslator)
{
    bool foundTranslation = false;
    // Configure translations
    for (const QString& path : PathInfo::translationsPaths()) {
        if (ConfigHandler().uiLanguage() == QStringLiteral("auto")) {
            // Load language, which was detected from the system
            foundTranslation =
              translator.load(QLocale(),
                              QStringLiteral("Internationalization"),
                              QStringLiteral("_"),
                              path);
        } else {
            // Load language from settings
            foundTranslation =
              translator.load(QStringLiteral("Internationalization_") +
                                ConfigHandler().uiLanguage(),
                              path);
        }
        if (foundTranslation) {
            break;
        }
    }
    if (!foundTranslation) {
        if (ConfigHandler().uiLanguage() == QStringLiteral("auto")) {
            QLocale l;
            qWarning() << QStringLiteral(
                            "No SnipSnap translation found for %1")
                            .arg(l.uiLanguages().join(", "));
        } else {
            qWarning() << QStringLiteral(
                            "No SnipSnap translation found for %1")
                            .arg(ConfigHandler().uiLanguage());
        }
    }

    if (ConfigHandler().uiLanguage() == QStringLiteral("auto")) {
        foundTranslation =
          qtTranslator.load(QLocale::system(),
                            "qt",
                            "_",
                            QLibraryInfo::path(QLibraryInfo::TranslationsPath));
    } else {
        foundTranslation = qtTranslator.load(
          QStringLiteral("qt_") + ConfigHandler().uiLanguage(),

          QLibraryInfo::path(QLibraryInfo::TranslationsPath));
    }
    if (!foundTranslation) {
        if (ConfigHandler().uiLanguage() == QStringLiteral("auto")) {
            qWarning() << QStringLiteral("No Qt translation found for %1")
                            .arg(QLocale::languageToString(
                              QLocale::system().language()));
        } else {
            qWarning() << QStringLiteral("No Qt translation found for %1")
                            .arg(ConfigHandler().uiLanguage());
        }
    }

    QCoreApplication::installTranslator(&translator);
    QCoreApplication::installTranslator(&qtTranslator);
}

void configureApp(bool gui, QTranslator& translator, QTranslator& qtTranslator)
{
    if (gui) {
        // Match the reverse-DNS desktop entry so Wayland compositors group
        // every SnipSnap window under the correct launcher and icon.
        QGuiApplication::setDesktopFileName(
          QStringLiteral("tech.norvi.snipsnap"));
#if defined(Q_OS_WIN) && QT_VERSION >= QT_VERSION_CHECK(6, 5, 0)
        QApplication::setStyle("Fusion"); // Supports dark scheme on Win 10/11
#else
        QApplication::setStyle(new StyleOverride);
#endif
    }

    auto app = QCoreApplication::instance();
    app->setAttribute(Qt::AA_DontCreateNativeWidgetSiblings, true);
    configureTranslation(translator, qtTranslator);
}

// TODO find a way so we don't have to do this
/// Recreate the application as a QApplication
void reinitializeAsQApplication(int& argc,
                                char* argv[],
                                QTranslator& translator,
                                QTranslator& qtTranslator)
{
    delete QCoreApplication::instance();
    new QApplication(argc, argv);
    configureApp(true, translator, qtTranslator);
}

/**
 * Carry settings across the rename.
 *
 * Qt derives the config path from the organization and application names set
 * below, so renaming those moves ~/.config/flameshot/flameshot.ini to
 * ~/.config/snipsnap/snipsnap.ini and everyone silently starts from defaults
 * on the upgrade that does it.
 *
 * Copies rather than moves: the old tree is left exactly as it was, so
 * downgrading to a pre-rename package finds its config intact. Existing files
 * in the new tree are never overwritten, while missing files are retried on a
 * later launch if a previous migration was interrupted.
 */
static void migrateLegacyConfig()
{
    const QString base = QStandardPaths::writableLocation(
      QStandardPaths::GenericConfigLocation);
    if (base.isEmpty()) {
        return;
    }
    const QDir legacy(base + QStringLiteral("/flameshot"));
    const QDir current(base + QStringLiteral("/snipsnap"));
    if (!legacy.exists()) {
        return;
    }
    if (!QDir().mkpath(current.path())) {
        return;
    }
    const QFileInfoList files = legacy.entryInfoList(QDir::Files);
    for (const QFileInfo& file : files) {
        const QString name = file.fileName() == QStringLiteral("flameshot.ini")
                               ? QStringLiteral("snipsnap.ini")
                               : file.fileName();
        const QString destination = current.filePath(name);
        // Never overwrite a user's new SnipSnap setting or a file created by
        // a racing instance. Retry only missing files on later launches.
        if (QFileInfo::exists(destination)) {
            continue;
        }
        if (!QFile::copy(file.absoluteFilePath(), destination)) {
            qWarning() << "Could not migrate legacy configuration file"
                       << file.absoluteFilePath() << "to" << destination;
        }
    }
}

int main(int argc, char* argv[])
{
    CaptureTrace::initializeProcessStart();

    QTranslator translator, qtTranslator;

    // Required for saving button list QList<CaptureTool::Type>
    qRegisterMetaType<QList<int>>();

    QCoreApplication::setApplicationVersion(APP_VERSION);
    QCoreApplication::setApplicationName(QStringLiteral("snipsnap"));
    QCoreApplication::setOrganizationName(QStringLiteral("snipsnap"));
    // Before anything constructs a QSettings against the new path.
    migrateLegacyConfig();
    // Deliberately NOT setting applicationDisplayName. Qt's
    // QPlatformWindow::formatWindowTitle() appends the display name to every
    // window title behind an em dash, which turns the bridge editor's title
    // into "SnipSnap [capture-id=N] - SnipSnap". The Shell extension matches
    // that window by exact title before transferring focus, so setting it
    // breaks the handoff protocol. Window titles already carry the product
    // name, and the desktop entry supplies it everywhere else.
    QNetworkProxyFactory::setUseSystemConfiguration(true);

    // no arguments, just launch SnipSnap
    if (argc == 1) {
        QApplication app(argc, argv);
        configureTranslation(translator, qtTranslator);

#ifdef USE_KDSINGLEAPPLICATION
#ifdef Q_OS_UNIX
        auto signalDaemon = SignalDaemon();
        QObject::connect(&signalDaemon,
                         &SignalDaemon::signalReceived,
                         QCoreApplication::instance(),
                         [](int signalNumber) {
                             QCoreApplication::exit(E_SIG_BASE + signalNumber);
                         });
        UnixSignalHandlers signalHandlers;
        if (signalHandlers.install() != 0) {
            AbstractLogger::error()
              << QObject::tr("Unable to install Unix signal handlers");
            return E_GENERAL;
        }
#endif
        auto kdsa =
          KDSingleApplication(QStringLiteral("tech.norvi.snipsnap"));

        if (!kdsa.isPrimaryInstance() &&
            !ConfigHandler().allowMultipleGuiInstances()) {
            return 0; // Quit
        }
#endif

        configureApp(true, translator, qtTranslator);
        auto c = SnipSnap::instance();
        SnipSnapDaemon::start();

#if defined(USE_KDSINGLEAPPLICATION) &&                                        \
  (defined(Q_OS_MACOS) || defined(Q_OS_WIN))
        if (kdsa.isPrimaryInstance()) {
            QObject::connect(
              &kdsa,
              &KDSingleApplication::messageReceived,
              SnipSnapDaemon::instance(),
              &SnipSnapDaemon::messageReceivedFromSecondaryInstance);
        }
#endif

#if !(defined(Q_OS_MACOS) || defined(Q_OS_WIN))
        new SnipSnapDBusAdapter(c);
        QDBusConnection dbus = QDBusConnection::sessionBus();
        if (!dbus.isConnected()) {
            AbstractLogger::error()
              << QObject::tr("Unable to connect via DBus");
        }
        dbus.registerObject(QStringLiteral("/"), c);
        dbus.registerService(QStringLiteral("tech.norvi.snipsnap"));
#endif
        return QCoreApplication::exec();
    }

    /*--------------|
     * CLI parsing  |
     * ------------*/
    new QCoreApplication(argc, argv);
    configureApp(false, translator, qtTranslator);

    CommandLineParser parser;
    // Add description
    parser.setDescription(
      QObject::tr("Powerful yet simple to use screenshot software."));
    parser.setGeneralErrorMessage(QObject::tr("See") + " snipsnap --help.");
    // Arguments
    CommandArgument fullArgument(
      QStringLiteral("full"),
      QObject::tr("Capture screenshot of all monitors at the same time."));
    CommandArgument launcherArgument(QStringLiteral("launcher"),
                                     QObject::tr("Open the capture launcher."));
    CommandArgument guiArgument(
      QStringLiteral("gui"),
      QObject::tr("Start a manual capture in GUI mode."));
    CommandArgument configArgument(QStringLiteral("config"),
                                   QObject::tr("Configure") + " snipsnap.");
    CommandArgument screenArgument(
      QStringLiteral("screen"),
      QObject::tr("Capture a screenshot of the specified monitor."));

    // Options
    CommandOption pathOption(
      { "p", "path" },
      QObject::tr("Existing directory or new file to save to"),
      QStringLiteral("path"));
    CommandOption clipboardOption(
      { "c", "clipboard" }, QObject::tr("Save the capture to the clipboard"));
    CommandOption pinOption("pin",
                            QObject::tr("Pin the capture to the screen"));
    CommandOption delayOption({ "d", "delay" },
                              QObject::tr("Delay time in milliseconds"),
                              QStringLiteral("milliseconds"));

    CommandOption useLastRegionOption(
      "last-region",
      QObject::tr("Repeat screenshot with previously selected region"));

    CommandOption regionOption("region",
                               QObject::tr("Screenshot region to select"),
                               QStringLiteral("WxH+X+Y or string"));
    CommandOption filenameOption({ "f", "filename" },
                                 QObject::tr("Set the filename pattern"),
                                 QStringLiteral("pattern"));
    CommandOption acceptOnSelectOption(
      { "s", "accept-on-select" },
      QObject::tr("Accept capture as soon as a selection is made"));
    CommandOption trayOption({ "t", "trayicon" },
                             QObject::tr("Enable or disable the trayicon"),
                             QStringLiteral("bool"));
    CommandOption autostartOption(
      { "a", "autostart" },
      QObject::tr("Enable or disable run at startup"),
      QStringLiteral("bool"));
    CommandOption notificationOption(
      { "n", "notifications" },
      QObject::tr("Enable or disable the notifications"),
      QStringLiteral("bool"));
    CommandOption checkOption(
      "check", QObject::tr("Check the configuration for errors"));
    CommandOption showHelpOption(
      { "s", "showhelp" },
      QObject::tr("Show the help message in the capture mode"),
      QStringLiteral("bool"));
    CommandOption mainColorOption({ "m", "maincolor" },
                                  QObject::tr("Define the main UI color"),
                                  QStringLiteral("color-code"));
    CommandOption contrastColorOption(
      { "k", "contrastcolor" },
      QObject::tr("Define the contrast UI color"),
      QStringLiteral("color-code"));
    CommandOption rawImageOption({ "r", "raw" },
                                 QObject::tr("Print raw PNG capture"));
    CommandOption selectionOption(
      { "g", "print-geometry" },
      QObject::tr("Print geometry of the selection in the format WxH+X+Y. Does "
                  "nothing if raw is specified"));
    CommandOption screenNumberOption(
      { "n", "number" },
      QObject::tr("Define the screen to capture (starting from 0)") + ",\n" +
        QObject::tr("default: screen containing the cursor"),
      QObject::tr("Screen number"),
      QStringLiteral("-1"));
    CommandOption editOption(
      { "e", "edit" },
      QObject::tr("Interactively select and edit the screenshot region"));

    // Add checkers
    auto colorChecker = [](const QString& colorCode) -> bool {
        QColor parsedColor(colorCode);
        return parsedColor.isValid() && parsedColor.alphaF() == 1.0;
    };
    QString colorErr =
      QObject::tr("Invalid color, "
                  "this flag supports the following formats:\n"
                  "- #RGB (each of R, G, and B is a single hex digit)\n"
                  "- #RRGGBB\n- #RRRGGGBBB\n"
                  "- #RRRRGGGGBBBB\n"
                  "- Named colors like 'blue' or 'red'\n"
                  "You may need to escape the '#' sign as in '\\#FFF'");

    const QString delayErr =
      QObject::tr("Invalid delay, it must be a number greater than 0");
    const QString numberErr =
      QObject::tr("Invalid screen number, it must be non negative");
    const QString regionErr = QObject::tr(
      "Invalid region, use 'WxH+X+Y' or 'all' or 'screen0/screen1/...'.");
    auto numericChecker = [](const QString& delayValue) -> bool {
        bool ok;
        int value = delayValue.toInt(&ok);
        return ok && value >= 0;
    };
    auto regionChecker = [](const QString& region) -> bool {
        Region valueHandler;
        return valueHandler.check(region);
    };

    const QString pathErr =
      QObject::tr("Invalid path, must be an existing directory or a new file "
                  "in an existing directory");
    auto pathChecker = [pathErr](const QString& pathValue) -> bool {
        QFileInfo fileInfo(pathValue);
        if (fileInfo.isDir() || fileInfo.dir().exists()) {
            return true;
        } else {
            AbstractLogger::error() << QObject::tr(pathErr.toLatin1().data());
            return false;
        }
    };

    const QString booleanErr =
      QObject::tr("Invalid value, it must be defined as 'true' or 'false'");
    auto booleanChecker = [](const QString& value) -> bool {
        return value == QLatin1String("true") ||
               value == QLatin1String("false");
    };

    contrastColorOption.addChecker(colorChecker, colorErr);
    mainColorOption.addChecker(colorChecker, colorErr);
    delayOption.addChecker(numericChecker, delayErr);
    regionOption.addChecker(regionChecker, regionErr);
    useLastRegionOption.addChecker(booleanChecker, booleanErr);
    pathOption.addChecker(pathChecker, pathErr);
    trayOption.addChecker(booleanChecker, booleanErr);
    autostartOption.addChecker(booleanChecker, booleanErr);
    notificationOption.addChecker(booleanChecker, booleanErr);
    showHelpOption.addChecker(booleanChecker, booleanErr);
    screenNumberOption.addChecker(numericChecker, numberErr);

    // Relationships
    parser.AddArgument(guiArgument);
    parser.AddArgument(screenArgument);
    parser.AddArgument(fullArgument);
    parser.AddArgument(launcherArgument);
    parser.AddArgument(configArgument);
    auto helpOption = parser.addHelpOption();
    auto versionOption = parser.addVersionOption();
    parser.AddOptions({ pathOption,
                        clipboardOption,
                        delayOption,
                        regionOption,
                        useLastRegionOption,
                        rawImageOption,
                        selectionOption,
                        pinOption,
                        acceptOnSelectOption },
                      guiArgument);
    parser.AddOptions({ screenNumberOption,
                        editOption,
                        clipboardOption,
                        pathOption,
                        delayOption,
                        regionOption,
                        rawImageOption,
                        pinOption },
                      screenArgument);
    parser.AddOptions(
      { pathOption, clipboardOption, delayOption, rawImageOption },
      fullArgument);
    parser.AddOptions({ autostartOption,
                        notificationOption,
                        filenameOption,
                        trayOption,
                        showHelpOption,
                        mainColorOption,
                        contrastColorOption,
                        checkOption },
                      configArgument);
    // Parse
    if (!parser.parse(QCoreApplication::arguments())) {
        goto finish;
    }

    // PROCESS DATA
    //--------------
    SnipSnap::setOrigin(SnipSnap::CLI);
    if (parser.isSet(helpOption) || parser.isSet(versionOption)) {
    } else if (parser.isSet(launcherArgument)) { // LAUNCHER
        reinitializeAsQApplication(argc, argv, translator, qtTranslator);
        SnipSnap* snipsnap = SnipSnap::instance();
        snipsnap->launcher();
        QCoreApplication::exec();
    } else if (parser.isSet(guiArgument)) { // GUI
        reinitializeAsQApplication(argc, argv, translator, qtTranslator);

        // Prevent multiple instances of 'snipsnap gui' from running if not
        // configured to do so.
        if (!ConfigHandler().allowMultipleGuiInstances()) {
            auto* mutex = guiMutexLock();
            if (!mutex) {
                return 1;
            }
            QObject::connect(QCoreApplication::instance(),
                             &QCoreApplication::aboutToQuit,
                             QCoreApplication::instance(),
                             [mutex]() {
                                 mutex->detach();
                                 delete mutex;
                             });
        }

        // Option values
        QString path = parser.value(pathOption);
        if (!path.isEmpty()) {
            path = QDir(path).absolutePath();
        }
        int delay = parser.value(delayOption).toInt();
        QString region = parser.value(regionOption);
        bool useLastRegion = parser.isSet(useLastRegionOption);
        bool clipboard = parser.isSet(clipboardOption);
        bool raw = parser.isSet(rawImageOption);
        bool printGeometry = parser.isSet(selectionOption);
        bool pin = parser.isSet(pinOption);
        bool acceptOnSelect = parser.isSet(acceptOnSelectOption);
        CaptureRequest req(CaptureRequest::GRAPHICAL_MODE, delay, path);
        if (!region.isEmpty()) {
            auto selectionRegion = Region().value(region).toRect();
            req.setInitialSelection(selectionRegion);
        } else if (useLastRegion) {
            req.setInitialSelection(getLastRegion());
        }
        if (clipboard) {
            req.addTask(CaptureRequest::COPY);
        }
        if (raw) {
            req.addTask(CaptureRequest::PRINT_RAW);
        }
        if (!path.isEmpty()) {
            req.addSaveTask(path);
        }
        if (printGeometry) {
            req.addTask(CaptureRequest::PRINT_GEOMETRY);
        }
        if (pin) {
            req.addTask(CaptureRequest::PIN);
        }
        if (acceptOnSelect) {
            req.addTask(CaptureRequest::ACCEPT_ON_SELECT);
            if (!clipboard && !raw && path.isEmpty() && !printGeometry &&
                !pin) {
                req.addSaveTask();
            }
        }
        int guiExitCode = requestCaptureAndWait(req);
        delete QCoreApplication::instance();
        return guiExitCode;
    } else if (parser.isSet(fullArgument)) { // FULL
        reinitializeAsQApplication(argc, argv, translator, qtTranslator);

        // Option values
        QString path = parser.value(pathOption);
        if (!path.isEmpty()) {
            path = QDir(path).absolutePath();
        }
        int delay = parser.value(delayOption).toInt();
        bool clipboard = parser.isSet(clipboardOption);
        bool raw = parser.isSet(rawImageOption);

        CaptureRequest req(CaptureRequest::FULLSCREEN_MODE, delay);
        if (clipboard) {
            req.addTask(CaptureRequest::COPY);
        }
        if (!path.isEmpty()) {
            req.addSaveTask(path);
        }
        if (raw) {
            req.addTask(CaptureRequest::PRINT_RAW);
        }
        if (!clipboard && path.isEmpty() && !raw) {
            req.addSaveTask();
        }
        {
            int fullExitCode = requestCaptureAndWait(req);
            delete QCoreApplication::instance();
            return fullExitCode;
        }
    } else if (parser.isSet(screenArgument)) { // SCREEN
        reinitializeAsQApplication(argc, argv, translator, qtTranslator);

        QString numberStr = parser.value(screenNumberOption);
        // Option values
        int screenNumber =
          numberStr.startsWith(QLatin1String("-")) ? -1 : numberStr.toInt();
        QString path = parser.value(pathOption);
        if (!path.isEmpty()) {
            path = QDir(path).absolutePath();
        }
        int delay = parser.value(delayOption).toInt();
        QString region = parser.value(regionOption);
        bool clipboard = parser.isSet(clipboardOption);
        bool raw = parser.isSet(rawImageOption);
        bool pin = parser.isSet(pinOption);
        bool edit = parser.isSet(editOption);

        CaptureRequest req(CaptureRequest::SCREEN_MODE, delay, screenNumber);
        if (edit) {
            req = CaptureRequest(CaptureRequest::GRAPHICAL_MODE, delay);
            if (screenNumber >= 0) {
                req.setSelectedMonitor(screenNumber);
            }
        }

        if (!region.isEmpty()) {
            if (region.startsWith("screen")) {
                AbstractLogger::error()
                  << "The 'screen' command does not support "
                     "'--region screen<N>'.\n"
                     "See snipsnap --help.\n";
                exit(1);
            }
            req.setInitialSelection(Region().value(region).toRect());
        }
        if (clipboard) {
            req.addTask(CaptureRequest::COPY);
        }
        if (raw) {
            req.addTask(CaptureRequest::PRINT_RAW);
        }
        if (!path.isEmpty()) {
            req.addSaveTask(path);
        }
        if (pin) {
            req.addTask(CaptureRequest::PIN);
        }

        if (!edit && !clipboard && !raw && path.isEmpty() && !pin) {
            req.addSaveTask();
        }

        {
            int screenExitCode = requestCaptureAndWait(req);
            delete QCoreApplication::instance();
            return screenExitCode;
        }
    } else if (parser.isSet(configArgument)) { // CONFIG
        bool autostart = parser.isSet(autostartOption);
        bool notification = parser.isSet(notificationOption);
        bool filename = parser.isSet(filenameOption);
        bool tray = parser.isSet(trayOption);
        bool mainColor = parser.isSet(mainColorOption);
        bool contrastColor = parser.isSet(contrastColorOption);
        bool check = parser.isSet(checkOption);
        bool someFlagSet = (autostart || notification || filename || tray ||
                            mainColor || contrastColor || check);
        if (check) {
            AbstractLogger err = AbstractLogger::error(AbstractLogger::Stderr);
            bool ok = ConfigHandler().checkForErrors(&err);
            if (ok) {
                AbstractLogger::info()
                  << QStringLiteral("No errors detected.\n");
                goto finish;
            } else {
                return 1;
            }
        }
        if (!someFlagSet) {
            // Open gui when no options are given
            reinitializeAsQApplication(argc, argv, translator, qtTranslator);
            auto* application =
              qobject_cast<QApplication*>(QCoreApplication::instance());
            QObject::connect(application,
                             &QApplication::lastWindowClosed,
                             application,
                             &QApplication::quit);
            SnipSnap::instance()->config();
            QCoreApplication::exec();
        } else {
            ConfigHandler config;

            if (autostart) {
                config.setStartupLaunch(parser.value(autostartOption) ==
                                        "true");
            }
            if (notification) {
                config.setShowDesktopNotification(
                  parser.value(notificationOption) == "true");
            }
            if (filename) {
                QString newFilename(parser.value(filenameOption));
                config.setFilenamePattern(newFilename);
                FileNameHandler fh;
                QTextStream(stdout)
                  << QStringLiteral("The new pattern is '%1'\n"
                                    "Parsed pattern example: %2\n")
                       .arg(newFilename, fh.parsedPattern());
            }
            if (tray) {
                config.setDisabledTrayIcon(parser.value(trayOption) == "false");
            }
            if (mainColor) {
                // TODO use value handler
                QString colorCode = parser.value(mainColorOption);
                QColor parsedColor(colorCode);
                config.setUiColor(parsedColor);
            }
            if (contrastColor) {
                QString colorCode = parser.value(contrastColorOption);
                QColor parsedColor(colorCode);
                config.setContrastUiColor(parsedColor);
            }
        }
    }
finish:
    delete QCoreApplication::instance();
    return 0;
}
