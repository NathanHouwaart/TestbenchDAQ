# Experimental Windows Gator recorder

This helper targets the private `gatorapi-3.3.0-x64` PhotonFirst/Technobis
runtime. It is deliberately separate from the Linux helper: it uses an older,
different vendor API, supports one attached Switched Gator, and has not yet
received hardware acceptance testing. Do not use it for production tests yet.

The helper accepts the same core options as the Linux recorder and emits the
same `gator_channel.csv` columns expected by TestbenchDAQ. It converts the
legacy API's wavelength values from picometres to femtometres. The legacy
continuous stream has a device-relative timestamp, so its timestamps are
estimated against host UTC at batch receipt time. The session manifest records
`timestamp_source: host_estimated` and `vendor_api: gatorapi-3.3.0`.

## Local build

Obtain the private vendor runtime through the MaintenanceLab runtime repository
and extract it locally; do not add it to this public repository. With Visual
Studio 2022 Build Tools and CMake installed, from the TestbenchDAQ checkout:

```powershell
cmake -S gator_recorder -B gator_recorder/build `
  -G "Visual Studio 17 2022" -A x64 `
  -DGATOR_API_DIR="C:/path/to/gatorapi-3.3.0-x64"
cmake --build gator_recorder/build --config Release
```

The build copies the required private DLLs beside `gator_recorder.exe` for
local testing only. It does not install or redistribute a runtime.

## Short hardware validation

With the Gator connected, make local copies of the two configuration examples.
In `machine.json`, set `output_root` to `C:\\TestbenchDAQ-data` and
`gator.binary_path` to `.\\gator_recorder\\build\\Release\\gator_recorder.exe`.
In `acquisition.json`, configure a diagnostic 10-second window and enable
Gator with the intended channel, full scale, and sample rate. Then run:

```powershell
Copy-Item machine.example.json machine.json
Copy-Item acquisition.example.json acquisition.json
python main.py `
  --machine machine.json `
  --acquisition acquisition.json `
  --allow-local-output
```

Inspect the resulting raw CSV, session manifest, and eight exported signal
files. Confirm the wavelength scale, timestamps, sample count, Ctrl+C cleanup,
and the physical channel before relying on the result.
