# from here:
#
# https://github.com/lefticus/cppbestpractices/blob/master/02-Use_the_Tools_Available.md

function(set_project_warnings project_name)
  option(SNIPSNAP_WARNINGS_AS_ERRORS "Treat SnipSnap compiler warnings as errors" OFF)

  set(MSVC_WARNINGS
      /W4 # Baseline reasonable warnings
      /permissive- # standards conformance mode for MSVC compiler.
  )

  set(CLANG_WARNINGS
      -Wall
      -Wextra
      -Wpedantic
      -Wformat=2
      # Qt virtual hooks commonly require parameters that an override does not
      # use. Keep the useful -Wextra baseline without forcing boilerplate casts.
      -Wno-unused-parameter
      # strftime intentionally consumes a validated, runtime-generated format.
      -Wno-format-nonliteral
  )

  if(SNIPSNAP_WARNINGS_AS_ERRORS)
    set(CLANG_WARNINGS ${CLANG_WARNINGS} -Werror)
    set(MSVC_WARNINGS ${MSVC_WARNINGS} /WX)
  endif()

  set(GCC_WARNINGS
      ${CLANG_WARNINGS}
      -Wmisleading-indentation
      -Wduplicated-cond
      -Wduplicated-branches
      -Wlogical-op
  )

  if(MSVC)
    set(PROJECT_WARNINGS ${MSVC_WARNINGS})
  elseif(CMAKE_CXX_COMPILER_ID MATCHES ".*Clang")
    set(PROJECT_WARNINGS ${CLANG_WARNINGS})
  elseif(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    set(PROJECT_WARNINGS ${GCC_WARNINGS})
  else()
    message(AUTHOR_WARNING "No compiler warnings set for '${CMAKE_CXX_COMPILER_ID}' compiler.")
  endif()

  target_compile_options(
    ${project_name}
    INTERFACE "$<$<COMPILE_LANGUAGE:CXX>:${PROJECT_WARNINGS}>")

endfunction()
