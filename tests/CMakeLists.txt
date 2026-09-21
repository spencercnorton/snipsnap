find_package(Qt6 CONFIG REQUIRED Core Gui Test Widgets)
find_package(Python3 COMPONENTS Interpreter QUIET)

set(CMAKE_AUTOMOC ON)

function(add_snipsnap_test target)
  add_test(NAME "unit.${target}" COMMAND ${target})
  set_tests_properties(
    "unit.${target}"
    PROPERTIES ENVIRONMENT "LC_ALL=C.UTF-8;QT_QPA_PLATFORM=offscreen"
               TIMEOUT 15)
endfunction()

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  find_package(Qt6 CONFIG REQUIRED DBus Network Widgets)
  find_program(DBUS_RUN_SESSION_EXECUTABLE dbus-run-session REQUIRED)

  add_executable(portal_screenshot_request_test
    portal_screenshot_request_test.cpp
    ${CMAKE_SOURCE_DIR}/src/config/cacheutils.cpp
    ${CMAKE_SOURCE_DIR}/src/utils/capturetrace.cpp
    ${CMAKE_SOURCE_DIR}/src/utils/portalscreenshotrequest.cpp)
  target_include_directories(portal_screenshot_request_test
    PRIVATE ${CMAKE_SOURCE_DIR}/src)
  target_link_libraries(
    portal_screenshot_request_test
    PRIVATE project_options project_warnings Qt6::Core Qt6::DBus Qt6::Gui
            Qt6::Test Qt6::Widgets)
  add_test(
    NAME unit.portal_screenshot_request_test
    COMMAND ${DBUS_RUN_SESSION_EXECUTABLE} --
            $<TARGET_FILE:portal_screenshot_request_test>)
  set_tests_properties(
    unit.portal_screenshot_request_test
    PROPERTIES ENVIRONMENT "LC_ALL=C.UTF-8;QT_QPA_PLATFORM=offscreen"
               TIMEOUT 15)

  add_executable(cache_permissions_test
    cache_permissions_test.cpp
    ${CMAKE_SOURCE_DIR}/src/config/cacheutils.cpp)
  target_include_directories(cache_permissions_test
    PRIVATE ${CMAKE_SOURCE_DIR}/src)
  target_link_libraries(
    cache_permissions_test PRIVATE project_options project_warnings Qt6::Core
                                   Qt6::Test)
  add_snipsnap_test(cache_permissions_test)

  if(Python3_Interpreter_FOUND)
    add_test(
      NAME unit.autostart_reconciliation_test
      COMMAND ${Python3_EXECUTABLE}
              ${CMAKE_CURRENT_SOURCE_DIR}/autostart_reconciliation_test.py
              $<TARGET_FILE:snipsnap>)
    set_tests_properties(
      unit.autostart_reconciliation_test
      PROPERTIES ENVIRONMENT "LC_ALL=C.UTF-8;QT_QPA_PLATFORM=offscreen"
                 TIMEOUT 15)
  endif()

  if(ENABLE_GNOME_SHELL_BRIDGE)
    add_executable(shell_bridge_server_smoke_test
      shell_bridge_server_smoke_test.cpp
      ${CMAKE_SOURCE_DIR}/src/core/shellbridgeprotocol.cpp)
    target_include_directories(shell_bridge_server_smoke_test
      PRIVATE ${CMAKE_SOURCE_DIR}/src)
    target_link_libraries(
      shell_bridge_server_smoke_test
      PRIVATE project_options project_warnings Qt6::Core Qt6::DBus Qt6::Network
              Qt6::Gui Qt6::Test)
    add_dependencies(shell_bridge_server_smoke_test snipsnap)
    add_test(
      NAME unit.shell_bridge_server_smoke_test
      COMMAND ${DBUS_RUN_SESSION_EXECUTABLE} --
              ${CMAKE_COMMAND} -E env
              "SNIPSNAP_TEST_EXECUTABLE=$<TARGET_FILE:snipsnap>"
              $<TARGET_FILE:shell_bridge_server_smoke_test>)
    set_tests_properties(
      unit.shell_bridge_server_smoke_test
      PROPERTIES ENVIRONMENT "LC_ALL=C.UTF-8"
                 TIMEOUT 15)
  endif()

  foreach(trace_io_test IN ITEMS capture_trace_file_test
                                 capture_trace_symlink_test)
    add_executable(${trace_io_test}
      ${trace_io_test}.cpp
      ${CMAKE_SOURCE_DIR}/src/config/cacheutils.cpp
      ${CMAKE_SOURCE_DIR}/src/utils/capturetrace.cpp)
    target_include_directories(${trace_io_test}
      PRIVATE ${CMAKE_SOURCE_DIR}/src)
    target_link_libraries(
      ${trace_io_test} PRIVATE project_options project_warnings Qt6::Core
                               Qt6::Test)
    add_snipsnap_test(${trace_io_test})
  endforeach()
endif()

add_executable(colorutils_test
  colorutils_test.cpp
  ${CMAKE_SOURCE_DIR}/src/utils/colorutils.cpp)
target_include_directories(colorutils_test PRIVATE ${CMAKE_SOURCE_DIR}/src)
target_link_libraries(
  colorutils_test PRIVATE project_options project_warnings Qt6::Gui Qt6::Test)
add_snipsnap_test(colorutils_test)

add_executable(region_editor_mapping_test region_editor_mapping_test.cpp)
target_include_directories(region_editor_mapping_test
  PRIVATE ${CMAKE_SOURCE_DIR}/src)
target_link_libraries(
  region_editor_mapping_test
  PRIVATE project_options project_warnings Qt6::Core Qt6::Gui Qt6::Test
          Qt6::Widgets)
add_snipsnap_test(region_editor_mapping_test)

add_executable(region_editor_viewport_test
  region_editor_viewport_test.cpp
  ${CMAKE_SOURCE_DIR}/src/widgets/capture/regioneditorviewport.cpp)
target_include_directories(region_editor_viewport_test
  PRIVATE ${CMAKE_SOURCE_DIR}/src)
target_link_libraries(
  region_editor_viewport_test
  PRIVATE project_options project_warnings Qt6::Core Qt6::Test)
add_snipsnap_test(region_editor_viewport_test)

add_executable(
  source_sampling_tools_test
  source_sampling_tools_test.cpp
  ${CMAKE_SOURCE_DIR}/src/tools/capturetool.h
  ${CMAKE_SOURCE_DIR}/src/tools/abstracttwopointtool.h
  ${CMAKE_SOURCE_DIR}/src/tools/abstracttwopointtool.cpp
  ${CMAKE_SOURCE_DIR}/src/tools/invert/inverttool.h
  ${CMAKE_SOURCE_DIR}/src/tools/invert/inverttool.cpp
  ${CMAKE_SOURCE_DIR}/src/tools/pixelate/pixelaterenderer.h
  ${CMAKE_SOURCE_DIR}/src/tools/pixelate/pixelaterenderer.cpp
  ${CMAKE_SOURCE_DIR}/src/utils/colorutils.cpp
  ${CMAKE_SOURCE_DIR}/src/utils/pathinfo.cpp)
target_include_directories(source_sampling_tools_test
  PRIVATE ${CMAKE_SOURCE_DIR}/src)
target_compile_definitions(source_sampling_tools_test
  PRIVATE APP_PREFIX="${CMAKE_INSTALL_PREFIX}")
target_link_libraries(
  source_sampling_tools_test
  PRIVATE project_options project_warnings Qt6::Core Qt6::Gui Qt6::Test
          Qt6::Widgets)
add_snipsnap_test(source_sampling_tools_test)

add_executable(capture_trace_test
  capture_trace_test.cpp
  ${CMAKE_SOURCE_DIR}/src/config/cacheutils.cpp
  ${CMAKE_SOURCE_DIR}/src/utils/capturetrace.cpp)
target_include_directories(capture_trace_test PRIVATE ${CMAKE_SOURCE_DIR}/src)
target_link_libraries(
  capture_trace_test PRIVATE project_options project_warnings Qt6::Core
                             Qt6::Test)
add_snipsnap_test(capture_trace_test)

add_executable(shell_bridge_protocol_test
  shell_bridge_protocol_test.cpp
  ${CMAKE_SOURCE_DIR}/src/core/shellbridgeprotocol.cpp)
target_include_directories(shell_bridge_protocol_test
  PRIVATE ${CMAKE_SOURCE_DIR}/src)
target_link_libraries(
  shell_bridge_protocol_test PRIVATE project_options project_warnings Qt6::Core
                                     Qt6::Test)
add_snipsnap_test(shell_bridge_protocol_test)

if(Python3_Interpreter_FOUND)
  add_test(
    NAME unit.icon_asset_test
    COMMAND ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/icon_asset_test.py)
  set_tests_properties(unit.icon_asset_test PROPERTIES TIMEOUT 15)

  add_test(
    NAME unit.product_identity_test
    COMMAND ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/product_identity_test.py)
  set_tests_properties(unit.product_identity_test PROPERTIES TIMEOUT 15)

  add_test(
    NAME unit.capture_trace_summary_test
    COMMAND ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/capture_trace_summary_test.py)
  set_tests_properties(unit.capture_trace_summary_test PROPERTIES TIMEOUT 15)

  add_test(
    NAME unit.gnome_shell_bridge_e2e_log_test
    COMMAND ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/gnome_shell_bridge_e2e_log_test.py)
  set_tests_properties(unit.gnome_shell_bridge_e2e_log_test PROPERTIES TIMEOUT 15)

  add_test(
    NAME unit.gnome_shell_bridge_mixed_e2e_log_test
    COMMAND ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/gnome_shell_bridge_mixed_e2e_log_test.py)
  set_tests_properties(
    unit.gnome_shell_bridge_mixed_e2e_log_test PROPERTIES TIMEOUT 15)

  add_test(
    NAME unit.gnome_shell_e2e_startup_retry_test
    COMMAND ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/gnome_shell_e2e_startup_retry_test.py)
  set_tests_properties(
    unit.gnome_shell_e2e_startup_retry_test PROPERTIES TIMEOUT 15)
endif()

if(UNIX AND USE_KDSINGLEAPPLICATION)
  add_executable(signaldaemon_test
    signaldaemon_test.cpp
    ${CMAKE_SOURCE_DIR}/src/core/signaldaemon.cpp)
  target_include_directories(signaldaemon_test
    PRIVATE ${CMAKE_SOURCE_DIR}/src)
  target_link_libraries(
    signaldaemon_test PRIVATE project_options project_warnings Qt6::Core
                              Qt6::Test)
  add_snipsnap_test(signaldaemon_test)
endif()

add_test(NAME cli.version COMMAND $<TARGET_FILE:snipsnap> --version)
set_tests_properties(
  cli.version
  PROPERTIES ENVIRONMENT "LC_ALL=C.UTF-8"
             PASS_REGULAR_EXPRESSION "SnipSnap v${PROJECT_VERSION}"
             TIMEOUT 10)

add_test(NAME cli.help COMMAND $<TARGET_FILE:snipsnap> --help)
set_tests_properties(
  cli.help
  PROPERTIES ENVIRONMENT "LC_ALL=C.UTF-8"
             PASS_REGULAR_EXPRESSION "Start a manual capture"
             TIMEOUT 10)

add_test(NAME cli.gui_help COMMAND $<TARGET_FILE:snipsnap> gui --help)
set_tests_properties(
  cli.gui_help
  PROPERTIES ENVIRONMENT "LC_ALL=C.UTF-8"
             PASS_REGULAR_EXPRESSION "Screenshot region to select"
             TIMEOUT 10)
