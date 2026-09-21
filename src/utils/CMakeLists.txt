# Required to generate MOC
target_sources(
  snipsnap
  PRIVATE abstractlogger.h
          capturetrace.h
          filenamehandler.h
          screengrabber.h
          systemnotification.h
          valuehandler.h
          strfparse.h
)

target_sources(
  snipsnap
  PRIVATE abstractlogger.cpp
          capturetrace.cpp
          filenamehandler.cpp
          screengrabber.cpp
          monitorpreview.cpp
          confighandler.cpp
          systemnotification.cpp
          valuehandler.cpp
          screenshotsaver.cpp
          globalvalues.cpp
          desktopfileparse.cpp
          desktopinfo.cpp
          pathinfo.cpp
          colorutils.cpp
          history.cpp
          strfparse.cpp
)

IF (UNIX AND NOT APPLE)
target_sources(
  snipsnap
  PRIVATE portalscreenshotrequest.h
          portalscreenshotrequest.cpp
)
ENDIF()

IF (WIN32)
  target_sources(
    snipsnap
    PRIVATE winlnkfileparse.cpp
  )
ENDIF()
