cmake_minimum_required(VERSION 3.28)
project("gator_recorder" VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED True)
set(CMAKE_CXX_EXTENSIONS OFF)

# The recorder is distributed independently of the PhotonFirst runtime. Do
# not embed the API directory used on the build computer as an ELF RUNPATH;
# the Linux installer registers the vendor library with ldconfig instead.
set(CMAKE_SKIP_RPATH TRUE)

# Path to the public GTR API package (directory containing include/ and libgtrlib*.so)
# Override with: cmake -DGTR_API_DIR=/path/to/raspberry-pi4 ..
if(NOT DEFINED GTR_API_DIR)
  set(GTR_API_DIR "" CACHE PATH "Path to the GTR API package directory (contains include/ and libgtrlib*.so)")
endif()

if(NOT GTR_API_DIR)
  message(FATAL_ERROR
    "GTR_API_DIR is not set.\n"
    "Run cmake with: -DGTR_API_DIR=<path/to/raspberry-pi4>\n"
    "  e.g. cmake -B build -DGTR_API_DIR=/home/wentelteef/Documents/public_gtr_api_v0.1.0/raspberry-pi4 ..")
endif()

if(NOT DEFINED GTRLIB_LIBRARY)
  file(GLOB GTRLIB_LIBRARY "${GTR_API_DIR}/libgtrlib-pub-shared-v*.*.*.so")
  if(NOT GTRLIB_LIBRARY)
    message(FATAL_ERROR "libgtrlib*.so not found in GTR_API_DIR='${GTR_API_DIR}'")
  endif()
endif()

add_executable(${PROJECT_NAME} main.cpp)
target_include_directories(${PROJECT_NAME} SYSTEM PUBLIC "${GTR_API_DIR}/include")
target_link_libraries(${PROJECT_NAME} PRIVATE ${GTRLIB_LIBRARY})
