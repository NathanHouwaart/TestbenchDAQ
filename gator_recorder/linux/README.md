# Linux Gator recorder

This is the supported Linux/AArch64 implementation using the PhotonFirst public
GTR C++ API. Normal installation is performed by the repository-level
`scripts/install_gator_linux.sh`; it obtains the authorised private runtime,
builds this helper through `gator_recorder/CMakeLists.txt`, and installs the
result system-wide.

For a manual development build, configure from the repository root:

```bash
cmake -S gator_recorder -B gator_recorder/build \
  -DGTR_API_DIR=/path/to/public_gtr_api_v0.1.0/raspberry-pi4
cmake --build gator_recorder/build
```

The vendor SDK is intentionally not part of this public repository.
