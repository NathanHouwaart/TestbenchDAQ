# Gator recorder

This module contains the small native process used by TestbenchDAQ to acquire
PhotonFirst Switched Gator data. Its platform implementations share one
executable name (`gator_recorder`), command-line contract, CSV schema, and
stdout readiness/status tokens. Python orchestration therefore does not need
to know the native platform.

- [Linux implementation](linux/README.md): current supported path for the
  PhotonFirst public GTR C++ API on AArch64.
- [Windows implementation](windows/README.md): experimental path for the
  older private `gatorapi-3.3.0-x64` API.

Configure from this directory. CMake selects the implementation matching the
host platform:

```text
gator_recorder/
├── linux/
│   ├── CMakeLists.txt
│   └── main.cpp
└── windows/
    ├── CMakeLists.txt
    └── main.cpp
```
