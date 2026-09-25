set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED TRUE)
set(CMAKE_CXX_EXTENSIONS OFF)

# The recorder is distributed independently of the PhotonFirst runtime. Do
# not embed the API directory used on the build computer as an ELF RUNPATH;
# the Linux installer registers the vendor library with ldconfig instead.
set(CMAKE_SKIP_RPATH TRUE)

set(GTR_API_DIR "" CACHE PATH
  "Path to the GTR API package directory (contains include/ and libgtrlib*.so)")
if(NOT GTR_API_DIR)
  message(FATAL_ERROR
    "GTR_API_DIR is not set.\n"
    "Run cmake with: -DGTR_API_DIR=<path/to/raspberry-pi4>")
endif()

file(GLOB GTRLIB_LIBRARY "${GTR_API_DIR}/libgtrlib-pub-shared-v*.*.*.so")
if(NOT GTRLIB_LIBRARY)
  message(FATAL_ERROR "libgtrlib*.so not found in GTR_API_DIR='${GTR_API_DIR}'")
endif()

add_executable(gator_recorder main.cpp)
target_include_directories(gator_recorder SYSTEM PRIVATE "${GTR_API_DIR}/include")
target_link_libraries(gator_recorder PRIVATE ${GTRLIB_LIBRARY})
