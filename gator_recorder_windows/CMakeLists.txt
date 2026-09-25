cmake_minimum_required(VERSION 3.28)
project(gator_recorder_windows VERSION 0.1.0 LANGUAGES CXX)

if(NOT WIN32)
  message(FATAL_ERROR "gator_recorder_windows is only built on Windows.")
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED TRUE)
set(CMAKE_CXX_EXTENSIONS OFF)

set(GATOR_API_DIR "" CACHE PATH
  "Path to private gatorapi-3.3.0-x64 package (contains include/, lib/, bin/)")
if(NOT GATOR_API_DIR)
  message(FATAL_ERROR
    "GATOR_API_DIR is not set. Pass -DGATOR_API_DIR=C:/path/to/gatorapi-3.3.0-x64")
endif()

set(SWITCHEDGATOR_LIBRARY "${GATOR_API_DIR}/lib/switchedgator.lib")
if(NOT EXISTS "${GATOR_API_DIR}/include/switchedgator.h")
  message(FATAL_ERROR "switchedgator.h not found in GATOR_API_DIR='${GATOR_API_DIR}'")
endif()
if(NOT EXISTS "${SWITCHEDGATOR_LIBRARY}")
  message(FATAL_ERROR "switchedgator.lib not found in GATOR_API_DIR='${GATOR_API_DIR}'")
endif()

add_executable(gator_recorder main.cpp)
target_include_directories(gator_recorder PRIVATE "${GATOR_API_DIR}/include")
target_link_libraries(gator_recorder PRIVATE "${SWITCHEDGATOR_LIBRARY}")

# Local development convenience only: this does not install or redistribute the
# runtime. The private SDK DLLs must be beside the executable while testing.
file(GLOB GATOR_RUNTIME_DLLS "${GATOR_API_DIR}/bin/*.dll")
add_custom_command(TARGET gator_recorder POST_BUILD
  COMMAND ${CMAKE_COMMAND} -E copy_if_different ${GATOR_RUNTIME_DLLS} $<TARGET_FILE_DIR:gator_recorder>
  COMMAND_EXPAND_LISTS)
