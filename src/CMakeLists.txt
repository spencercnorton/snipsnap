find_package(
        Qt${QT_VERSION_MAJOR}
        CONFIG
        REQUIRED
        Core
        Gui
        Widgets
        Network
        Svg
        LinguistTools
)

if (UNIX)
find_package(
        Qt${QT_VERSION_MAJOR}
        CONFIG
        REQUIRED
        DBus
)
endif()

if (USE_WAYLAND_CLIPBOARD)
    find_package(KF6GuiAddons)
endif()

set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_AUTOUIC ON)

# set application icon
if (APPLE)
    set(SNIPSNAP_ICONSET ${CMAKE_BINARY_DIR}/snipsnap.iconset)
    set(SNIPSNAP_ICNS ${CMAKE_BINARY_DIR}/snipsnap.icns)

    # generate iconset
    execute_process(
            COMMAND bash "-c" "mkdir -p \"${SNIPSNAP_ICONSET}\""
    )

    execute_process(
            COMMAND bash "-c" "sips -z 16 16     \"${CMAKE_SOURCE_DIR}\"/data/img/app/tech.norvi.snipsnap-1024.png --out \"${SNIPSNAP_ICONSET}\"/icon_16x16.png"
            COMMAND bash "-c" "sips -z 32 32     \"${CMAKE_SOURCE_DIR}\"/data/img/app/tech.norvi.snipsnap-1024.png --out \"${SNIPSNAP_ICONSET}\"/icon_16x16@2x.png"
            COMMAND bash "-c" "sips -z 32 32     \"${CMAKE_SOURCE_DIR}\"/data/img/app/tech.norvi.snipsnap-1024.png --out \"${SNIPSNAP_ICONSET}\"/icon_32x32.png"
            COMMAND bash "-c" "sips -z 64 64     \"${CMAKE_SOURCE_DIR}\"/data/img/app/tech.norvi.snipsnap-1024.png --out \"${SNIPSNAP_ICONSET}\"/icon_32x32@2x.png"
            COMMAND bash "-c" "sips -z 128 128   \"${CMAKE_SOURCE_DIR}\"/data/img/app/tech.norvi.snipsnap-1024.png --out \"${SNIPSNAP_ICONSET}\"/icon_128x128.png"
            COMMAND bash "-c" "sips -z 256 256   \"${CMAKE_SOURCE_DIR}\"/data/img/app/tech.norvi.snipsnap-1024.png --out \"${SNIPSNAP_ICONSET}\"/icon_128x128@2x.png"
            COMMAND bash "-c" "sips -z 256 256   \"${CMAKE_SOURCE_DIR}\"/data/img/app/tech.norvi.snipsnap-1024.png --out \"${SNIPSNAP_ICONSET}\"/icon_256x256.png"
            COMMAND bash "-c" "sips -z 512 512   \"${CMAKE_SOURCE_DIR}\"/data/img/app/tech.norvi.snipsnap-1024.png --out \"${SNIPSNAP_ICONSET}\"/icon_256x256@2x.png"
            COMMAND bash "-c" "sips -z 512 512   \"${CMAKE_SOURCE_DIR}\"/data/img/app/tech.norvi.snipsnap-1024.png --out \"${SNIPSNAP_ICONSET}\"/icon_512x512.png"
            COMMAND bash "-c" "sips -z 1024 1024 \"${CMAKE_SOURCE_DIR}\"/data/img/app/tech.norvi.snipsnap-1024.png --out \"${SNIPSNAP_ICONSET}\"/icon_512x512@2x.png"

            COMMAND bash "-c" "iconutil -o \"${SNIPSNAP_ICNS}\" -c icns \"${SNIPSNAP_ICONSET}\""
    )

    execute_process(
        COMMAND bash "-c" "rm -R \"${SNIPSNAP_ICONSET}\""
    )

    execute_process(
        # copy icon from cache generated on the localhost if generation on CI failed
        COMMAND bash "-c" "[[ -r '\"${SNIPSNAP_ICNS}\"' ]] || cp \"${CMAKE_SOURCE_DIR}\"/packaging/macos/snipsnap.icns \"${SNIPSNAP_ICNS}\""
    )

    # Set application icon
    set(MACOSX_BUNDLE_ICON_FILE snipsnap.icns)

    # And this part tells CMake where to find and install the file itself
    set(APP_ICON_MACOSX ${SNIPSNAP_ICNS})
    set_source_files_properties(${APP_ICON_MACOSX} PROPERTIES MACOSX_PACKAGE_LOCATION "Resources")

    add_executable(snipsnap MACOSX_BUNDLE main.cpp ${APP_ICON_MACOSX})
else ()
    add_executable(snipsnap)
endif ()

add_executable(SnipSnap::snipsnap ALIAS snipsnap)

if(SNIPSNAP_ICON)
  target_compile_definitions(snipsnap PUBLIC SNIPSNAP_ICON="${SNIPSNAP_ICON}")
endif()

if (WIN32)
    add_executable(snipsnap-cli)
    target_sources(
        snipsnap-cli
        PRIVATE
        windows-cli.cpp)
    set_target_properties(snipsnap-cli PROPERTIES OUTPUT_NAME "snipsnap-cli")
    target_link_options(snipsnap-cli PRIVATE /SUBSYSTEM:CONSOLE)
    set_property(TARGET snipsnap PROPERTY WIN32_EXECUTABLE true)
    # Getting error D8016 - /utf-8 automatically set by current cmake/msvc ?
    #if (MSVC)
    #    target_compile_options(snipsnap PRIVATE /source-charset:utf-8)
    #endif ()
endif ()

if(MSVC)
    OPTION(USE_MP "use multiple" ON)
    OPTION(ProjectConfig_Global_COMPILE_FLAGS_WITH_MP
            "Set The Global Option COMPILE_FLAGS /MP to target." ON)
    if(ProjectConfig_Global_COMPILE_FLAGS_WITH_MP OR USE_MP)
        set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} /MP")
        set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} /MP")
    endif()
endif()

add_subdirectory(cli)
add_subdirectory(config)
add_subdirectory(core)
add_subdirectory(utils)
add_subdirectory(widgets)
add_subdirectory(tools)


set(SNIPSNAP_TS_FILES
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_bg.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_ca.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_cs.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_da.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_de_DE.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_el.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_en.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_es.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_et.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_eu.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_fa.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_fi.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_fr.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_ga.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_gl.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_grc.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_he.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_hu.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_id.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_it_IT.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_ja.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_ka.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_ko.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_nb_NO.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_nl_NL.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_nl.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_pt.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_pl.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_pt_BR.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_ro.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_ru.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_sk.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_sr_SP.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_sv_SE.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_sw.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_ta.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_th.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_tr.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_uk.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_vi.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_zh_CN.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_zh_HK.ts
      ${CMAKE_SOURCE_DIR}/data/translations/Internationalization_zh_TW.ts
         )

if (GENERATE_TS)
  qt6_create_translation(QM_FILES ${CMAKE_SOURCE_DIR} ${SNIPSNAP_TS_FILES})
else ()
  qt6_add_translation(QM_FILES ${SNIPSNAP_TS_FILES})
endif ()

target_sources(
        snipsnap
        PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/../data/graphics.qrc
        ${QM_FILES}
        main.cpp)
if (WIN32 AND EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/../data/snipsnap.rc)
    # The Windows version resource is not part of the published tree; without
    # it a Windows build simply has no embedded icon and version block.
    target_sources(snipsnap PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../data/snipsnap.rc)
endif ()


target_include_directories(snipsnap PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/>  # src/
    $<BUILD_INTERFACE:${CMAKE_BINARY_DIR}/>
)

target_link_libraries(
        snipsnap
        project_warnings
        project_options
        Qt${QT_VERSION_MAJOR}::Svg
        Qt${QT_VERSION_MAJOR}::Network
        Qt${QT_VERSION_MAJOR}::Widgets
        QtColorWidgets
)
if (UNIX)
target_link_libraries(
        snipsnap
        Qt${QT_VERSION_MAJOR}::DBus
)
endif()

if (USE_KDSINGLEAPPLICATION)
  message(STATUS "KDSingleApplication is used!")
  add_compile_definitions(USE_KDSINGLEAPPLICATION=1)

  if (USE_BUNDLED_KDSINGLEAPPLICATION)
    target_include_directories(snipsnap PRIVATE ${kdsingleapplication_SOURCE_DIR} ${kdsingleapplication_BINARY_DIR})
  endif()

  target_link_libraries(
          snipsnap
          KDAB::kdsingleapplication
  )
endif()

if (USE_WAYLAND_CLIPBOARD)
  target_compile_definitions(snipsnap PRIVATE USE_WAYLAND_CLIPBOARD=1)
  target_link_libraries(snipsnap KF6::GuiAddons)
endif()

if (APPLE)
    set_target_properties(snipsnap PROPERTIES
        MACOSX_BUNDLE TRUE
        MACOSX_BUNDLE_BUNDLE_NAME "SnipSnap"
        MACOSX_BUNDLE_BUNDLE_VERSION ${PROJECT_VERSION}
        MACOSX_BUNDLE_SHORT_VERSION_STRING ${PROJECT_VERSION}
        MACOSX_BUNDLE_IDENTIFIER "tech.norvi.snipsnap"
        MACOSX_BUNDLE_GUI_IDENTIFIER "tech.norvi.snipsnap"
    )
    set_property(TARGET snipsnap PROPERTY
        MACOSX_BUNDLE_INFO_PLIST ${CMAKE_SOURCE_DIR}/packaging/macos/Info.plist.in
    )
    target_link_libraries(
            snipsnap
            qhotkey
    )
endif ()


set(_src_root_path ${CMAKE_CURRENT_SOURCE_DIR})
file(GLOB_RECURSE _source_list LIST_DIRECTORIES false
    "${_src_root_path}/*.cpp"
    "${_src_root_path}/*.h")

#  will be organized according to the actual directory structure, .h.cpp is put together
source_group(TREE ${_src_root_path} FILES ${_source_list})

if (WIN32)
    set(USE_OPENSSL FALSE)
    if (ENABLE_OPENSSL)
        find_package(OpenSSL)
        if (OPENSSL_FOUND)
            message(STATUS "OpenSSL support enabled.")
            set(USE_OPENSSL TRUE)
        endif ()
    else ()
        mark_as_advanced(CLEAR OPENSSL_LIBRARIES OPENSSL_INCLUDE_DIR)
    endif ()

    if (NOT USE_OPENSSL)
        message(WARNING "OpenSSL is required to upload screenshots")
    endif ()

    target_link_libraries(
            snipsnap
            qhotkey
    )
endif ()

# Choose default color palette (small or large)
if($ENV{SNIPSNAP_PREDEFINED_COLOR_PALETTE_LARGE})
    set(SNIPSNAP_PREDEFINED_COLOR_PALETTE_LARGE true)
else()
    set(SNIPSNAP_PREDEFINED_COLOR_PALETTE_LARGE false)
endif()
message("SnipSnap predefined color palette large: " ${SNIPSNAP_PREDEFINED_COLOR_PALETTE_LARGE})
target_compile_definitions(snipsnap PRIVATE PREDEFINED_COLOR_PALETTE_LARGE=${SNIPSNAP_PREDEFINED_COLOR_PALETTE_LARGE})


find_package (Git)
if( DEFINED ENV{GIT_HASH})
    message("Using provided git_commit_hash: $ENV{GIT_HASH}")
    set(SNIPSNAP_GIT_HASH $ENV{GIT_HASH})
    target_compile_definitions(snipsnap PRIVATE SNIPSNAP_GIT_HASH="${SNIPSNAP_GIT_HASH}")
elseif (GIT_FOUND)
    message("git found: ${GIT_EXECUTABLE} in version     ${GIT_VERSION_STRING}")
    execute_process(COMMAND ${GIT_EXECUTABLE} rev-parse --short HEAD OUTPUT_VARIABLE SNIPSNAP_GIT_HASH)
    string(REGEX REPLACE "\r*\n$" "" SNIPSNAP_GIT_HASH "${SNIPSNAP_GIT_HASH}")
    target_compile_definitions(snipsnap PRIVATE SNIPSNAP_GIT_HASH="${SNIPSNAP_GIT_HASH}")
    message("SNIPSNAP_GIT_HASH: ${SNIPSNAP_GIT_HASH}")
else()
    target_compile_definitions(snipsnap PRIVATE SNIPSNAP_GIT_HASH="-")
    message(WARNING "Compiling without git commit hash")
endif ()

target_compile_definitions(snipsnap PRIVATE APP_PREFIX="${CMAKE_INSTALL_PREFIX}")
target_compile_definitions(snipsnap PRIVATE APP_VERSION="v${PROJECT_VERSION}")
#target_compile_definitions(snipsnap PRIVATE QAPPLICATION_CLASS=QApplication)
target_compile_definitions(snipsnap PRIVATE SNIPSNAP_APP_VERSION_URL="${GIT_API_URL}")
# Enable easier debugging of screenshot capture mode
if (SNIPSNAP_DEBUG_CAPTURE)
    target_compile_definitions(snipsnap PRIVATE SNIPSNAP_DEBUG_CAPTURE)
endif ()

if (USE_MONOCHROME_ICON)
    target_compile_definitions(snipsnap PRIVATE USE_MONOCHROME_ICON)
endif ()

foreach (FILE ${QM_FILES})
    get_filename_component(F_NAME ${FILE} NAME)
    add_custom_command(
            TARGET snipsnap
            POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy ${CMAKE_CURRENT_BINARY_DIR}/${F_NAME}
            ${CMAKE_CURRENT_BINARY_DIR}/translations/${F_NAME})
endforeach ()

# ######################################################################################################################
# Installation instructions

include(GNUInstallDirs)
set(INSTALL_CONFIGDIR ${CMAKE_INSTALL_LIBDIR}/cmake/SnipSnap)

if(USE_LAUNCHER_ABSOLUTE_PATH)
    set(LAUNCHER_EXECUTABLE "${CMAKE_INSTALL_FULL_BINDIR}/snipsnap")
else()
    set(LAUNCHER_EXECUTABLE "snipsnap")
endif()

# Install binary
install(TARGETS snipsnap
        EXPORT snipsnap-targets
        BUNDLE DESTINATION ${CMAKE_INSTALL_BINDIR}
        RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
if (WIN32)
    install(TARGETS snipsnap-cli
            RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
endif ()

if (UNIX)
    # Install desktop files, completion and dbus files
    configure_file(${CMAKE_SOURCE_DIR}/data/desktopEntry/package/tech.norvi.snipsnap.desktop
            ${CMAKE_CURRENT_BINARY_DIR}/share/applications/tech.norvi.snipsnap.desktop @ONLY)

    configure_file(${CMAKE_SOURCE_DIR}/data/appdata/tech.norvi.snipsnap.metainfo.xml
            ${CMAKE_CURRENT_BINARY_DIR}/share/metainfo/tech.norvi.snipsnap.metainfo.xml COPYONLY)

    configure_file(${CMAKE_SOURCE_DIR}/data/shell-completion/snipsnap.bash
            ${CMAKE_CURRENT_BINARY_DIR}/share/bash-completion/completions/snipsnap COPYONLY)

    configure_file(${CMAKE_SOURCE_DIR}/data/shell-completion/snipsnap.zsh
            ${CMAKE_CURRENT_BINARY_DIR}/share/zsh/site-functions/_snipsnap COPYONLY)

    configure_file(${CMAKE_SOURCE_DIR}/data/shell-completion/snipsnap.fish
            ${CMAKE_CURRENT_BINARY_DIR}/share/fish/vendor_completions.d/snipsnap.fish COPYONLY)

    configure_file(${CMAKE_SOURCE_DIR}/data/dbus/tech.norvi.snipsnap.xml
            ${CMAKE_CURRENT_BINARY_DIR}/share/dbus-1/interfaces/tech.norvi.snipsnap.xml COPYONLY)

    configure_file(${CMAKE_SOURCE_DIR}/data/dbus/tech.norvi.snipsnap.service.in
            ${CMAKE_CURRENT_BINARY_DIR}/share/dbus-1/services/tech.norvi.snipsnap.service)

    # Install man pages
    configure_file(${CMAKE_SOURCE_DIR}/data/man/man1/snipsnap.1
            ${CMAKE_CURRENT_BINARY_DIR}/share/man/man1/snipsnap.1 COPYONLY)

    # Install Icons
    configure_file(${CMAKE_SOURCE_DIR}/data/img/hicolor/48x48/apps/tech.norvi.snipsnap.png
            ${CMAKE_CURRENT_BINARY_DIR}/share/icons/hicolor/48x48/apps/tech.norvi.snipsnap.png COPYONLY)

    configure_file(${CMAKE_SOURCE_DIR}/data/img/hicolor/128x128/apps/tech.norvi.snipsnap.png
            ${CMAKE_CURRENT_BINARY_DIR}/share/icons/hicolor/128x128/apps/tech.norvi.snipsnap.png COPYONLY)

    configure_file(${CMAKE_SOURCE_DIR}/data/img/hicolor/scalable/apps/tech.norvi.snipsnap.svg
            ${CMAKE_CURRENT_BINARY_DIR}/share/icons/hicolor/scalable/apps/tech.norvi.snipsnap.svg COPYONLY)

    # Install icon with both names
    configure_file(${CMAKE_SOURCE_DIR}/data/img/hicolor/48x48/apps/snipsnap.png
            ${CMAKE_CURRENT_BINARY_DIR}/share/icons/hicolor/48x48/apps/snipsnap.png COPYONLY)

    configure_file(${CMAKE_SOURCE_DIR}/data/img/hicolor/128x128/apps/snipsnap.png
            ${CMAKE_CURRENT_BINARY_DIR}/share/icons/hicolor/128x128/apps/snipsnap.png COPYONLY)

    configure_file(${CMAKE_SOURCE_DIR}/data/img/hicolor/scalable/apps/snipsnap.svg
            ${CMAKE_CURRENT_BINARY_DIR}/share/icons/hicolor/scalable/apps/snipsnap.svg COPYONLY)

    # Let desktop themes recolour the symbolic status icon, while retaining the
    # older app-context name as a compatibility fallback.
    configure_file(${CMAKE_SOURCE_DIR}/data/img/hicolor/scalable/apps/snipsnap-tray.svg
            ${CMAKE_CURRENT_BINARY_DIR}/share/icons/hicolor/scalable/apps/snipsnap-tray.svg COPYONLY)
    configure_file(${CMAKE_SOURCE_DIR}/data/img/hicolor/scalable/status/snipsnap-tray-symbolic.svg
            ${CMAKE_CURRENT_BINARY_DIR}/share/icons/hicolor/scalable/status/snipsnap-tray-symbolic.svg COPYONLY)

    # Install assets
    install(DIRECTORY ${CMAKE_CURRENT_BINARY_DIR}/share/ DESTINATION ${CMAKE_INSTALL_DATAROOTDIR})

    # Install Translations
    install(FILES ${QM_FILES} DESTINATION ${CMAKE_INSTALL_DATAROOTDIR}/snipsnap/translations)
endif ()

# windeployqt
if (WIN32)
    # Add CMAKE_PREFIX_PATH to search paths
    foreach(prefix ${CMAKE_PREFIX_PATH})
        list(APPEND WINDEPLOYQT_SEARCH_PATHS "${prefix}/bin")
    endforeach()
    list(APPEND WINDEPLOYQT_SEARCH_PATHS "$ENV{QTDIR}/bin")

    find_program(WINDEPLOYQT_EXE windeployqt.exe
        PATHS ${WINDEPLOYQT_SEARCH_PATHS}
    )

    if(WINDEPLOYQT_EXE)
        message(STATUS "windeployqt found: ${WINDEPLOYQT_EXE}")

        if (CMAKE_BUILD_TYPE MATCHES Release)
            set(BINARIES_TYPE --release)
        else ()
            set(BINARIES_TYPE --debug)
        endif ()

        add_custom_command(
                TARGET snipsnap
                POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E remove_directory ${CMAKE_BINARY_DIR}/windeployqt_stuff
                COMMAND
                ${WINDEPLOYQT_EXE} ${BINARIES_TYPE} --no-translations --compiler-runtime --no-system-d3d-compiler
                --no-quick-import --dir ${CMAKE_BINARY_DIR}/windeployqt_stuff $<TARGET_FILE:snipsnap>
                # copy translations manually QM_FILES
                COMMAND ${CMAKE_COMMAND} -E make_directory ${CMAKE_BINARY_DIR}/windeployqt_stuff/translations
                COMMAND ${CMAKE_COMMAND} -E copy_directory ${CMAKE_BINARY_DIR}/src/translations
                ${CMAKE_BINARY_DIR}/windeployqt_stuff/translations)

        install(DIRECTORY ${CMAKE_BINARY_DIR}/windeployqt_stuff/ DESTINATION bin)


        STRING(REGEX REPLACE "\\\\" "/" OPENSSL_ROOT_DIR "$ENV{OPENSSL_ROOT_DIR}" )

        if (ENABLE_OPENSSL)
            if (EXISTS ${OPENSSL_ROOT_DIR}/bin)

                install(
                        DIRECTORY ${OPENSSL_ROOT_DIR}/bin/
                        DESTINATION bin
                        FILES_MATCHING
                        PATTERN "*.dll")
            else ()
                message(WARNING "Unable to find OpenSSL dlls.")
            endif ()
        endif ()

    else ()
        message(WARNING "Unable to find executable windeployqt.")
    endif ()
endif ()

# macdeployqt
if (APPLE)
# Code signing settings - optional, set to empty string to skip signing
    set(CODE_SIGN_IDENTITY "" CACHE STRING "Code signing identity (leave empty to skip signing)")
    set(DMG_SIGN_IDENTITY "" CACHE STRING "DMG signing identity (leave empty to skip signing)")
    # Custom target to create DMG (signed or unsigned)
    add_custom_target(create_dmg
    COMMAND ${CMAKE_CURRENT_SOURCE_DIR}/../packaging/macos/create_dmg.sh
            "${CMAKE_CURRENT_BINARY_DIR}/SnipSnap.app"
            "${CMAKE_CURRENT_BINARY_DIR}/SnipSnap-${PROJECT_VERSION}.dmg"
            "${CODE_SIGN_IDENTITY}"
            "${DMG_SIGN_IDENTITY}"
    DEPENDS snipsnap
    WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR}
    COMMENT "Creating DMG"
    VERBATIM
    )

# Always sign the app bundle (either with identity or ad hoc)
    if(CODE_SIGN_IDENTITY AND NOT CODE_SIGN_IDENTITY STREQUAL "")
        # Identity-based signing (requires Developer ID)
        add_custom_command(TARGET snipsnap POST_BUILD
            COMMAND codesign --force --deep --sign "${CODE_SIGN_IDENTITY}"
                    --options runtime --timestamp "$<TARGET_BUNDLE_DIR:snipsnap>"
            COMMENT "Code signing app bundle with ${CODE_SIGN_IDENTITY}"
        )
    else()
        # Ad hoc signing
        add_custom_command(TARGET snipsnap POST_BUILD
            COMMAND codesign --force --deep --sign - "$<TARGET_BUNDLE_DIR:snipsnap>"
            COMMENT "Ad hoc code signing app bundle (no identity required)"
        )
    endif()
   # Deploy Qt libraries and dependencies
   find_program(MACDEPLOYQT_EXECUTABLE macdeployqt HINTS ${Qt6_DIR}/../../../bin)

   if(MACDEPLOYQT_EXECUTABLE)
       add_custom_command(TARGET snipsnap POST_BUILD
           COMMAND ${MACDEPLOYQT_EXECUTABLE} "$<TARGET_BUNDLE_DIR:snipsnap>" -verbose=2
           COMMENT "Deploying Qt libraries"
       )

       # Re-sign after macdeployqt (it modifies the bundle)
       if(CODE_SIGN_IDENTITY AND NOT CODE_SIGN_IDENTITY STREQUAL "")
           add_custom_command(TARGET snipsnap POST_BUILD
               COMMAND codesign --force --deep --sign "${CODE_SIGN_IDENTITY}"
                       --options runtime --timestamp "$<TARGET_BUNDLE_DIR:snipsnap>"
               COMMENT "Re-signing app bundle after Qt deployment"
           )
       else()
           add_custom_command(TARGET snipsnap POST_BUILD
               COMMAND codesign --force --deep --sign - "$<TARGET_BUNDLE_DIR:snipsnap>"
               COMMENT "Re-signing app bundle after Qt deployment (ad hoc)"
           )
       endif()
   else()
       message(WARNING "macdeployqt not found. App may not run on systems without Qt installed.")
   endif()


endif ()
