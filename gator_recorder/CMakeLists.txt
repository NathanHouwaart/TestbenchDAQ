cmake_minimum_required(VERSION 3.28)
project(gator_recorder VERSION 0.1.0 LANGUAGES CXX)

# Both platform helpers deliberately expose the same executable name and CLI.
# Keep the native runtime implementation behind the platform subdirectory.
# Keep the executable at the build root so the Linux installer and Windows
# local-build instructions share the same predictable output location.
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}")
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  add_subdirectory(linux)
elseif(WIN32)
  add_subdirectory(windows)
else()
  message(FATAL_ERROR "gator_recorder supports Linux or Windows only.")
endif()
