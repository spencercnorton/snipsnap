target_sources(snipsnap PRIVATE
    snipsnap.h
    snipsnapdaemon.h
    qguiappcurrentscreen.h
)

target_sources(snipsnap PRIVATE
    capturerequest.cpp
    snipsnap.cpp
    snipsnapdaemon.cpp
    qguiappcurrentscreen.cpp
    shellbridgeprotocol.cpp
)

target_sources(snipsnap PRIVATE shellbridgeprotocol.h)

if (ENABLE_GNOME_SHELL_BRIDGE)
target_sources(snipsnap PRIVATE
    shellbridgeserver.h
    shellbridgeserver.cpp
)
endif ()

if (UNIX AND NOT APPLE)
target_sources(snipsnap PRIVATE
    snipsnapdbusadapter.h
    snipsnapdbusadapter.cpp
)
endif ()

if (USE_KDSINGLEAPPLICATION)
if (NOT WIN32)
target_sources(snipsnap PRIVATE
    signaldaemon.h
    signaldaemon.cpp
)
endif ()
endif ()

if (WIN32)
    target_sources(snipsnap PRIVATE globalshortcutfilter.h globalshortcutfilter.cpp)
endif ()
