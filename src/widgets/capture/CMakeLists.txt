# Required to generate MOC
target_sources(
        snipsnap
        PRIVATE buttonhandler.h
        capturebutton.h
        capturetoolbutton.h
        capturewidget.h
        colorpicker.h
        hovereventfilter.h
        overlaymessage.h
        selectionwidget.h
        magnifierwidget.h
        notifierbox.h
        regioneditorviewport.h
        regioneditorwindow.h
        modificationcommand.h)

target_sources(
        snipsnap
        PRIVATE buttonhandler.cpp
        capturebutton.cpp
        capturetoolbutton.cpp
        capturewidget.cpp
        colorpicker.cpp
        hovereventfilter.cpp
        overlaymessage.cpp
        notifierbox.cpp
        regioneditorviewport.cpp
        regioneditorwindow.cpp
        selectionwidget.cpp
        magnifierwidget.cpp
        modificationcommand.cpp)
