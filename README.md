# TestbenchDAQ

TestbenchDAQ coordinates a PhotonFirst Gator FBG interrogator and an enDAQ
recorder from one Linux command-line application.

The current Phase 2 release adds reliable enDAQ dismount/remount handling,
per-run verified IDE offload, storage preflight, explicit recorder controls,
and deferred prognostic signal export. Signal CSV files are currently an interim
representation. Complete cross-device time normalization and ISA-PHM metadata
serialization are subsequent phases.

## Supported platform

- Linux on AArch64
- Python 3.11 or newer
- PhotonFirst public GTR C++ API
- `endaq` and `endaq-device` Python packages

Windows is not currently supported.

## Install

Create a virtual environment and install the application:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Build the Gator recorder, replacing the API path if necessary:

```bash
cmake -S gator_recorder -B gator_recorder/build \
  -DGTR_API_DIR=/home/wentelteef/Documents/public_gtr_api_v0.1.0/raspberry-pi4
cmake --build gator_recorder/build
```

Install the Gator USB access rule, reload udev, and reconnect the Gator:

```bash
sudo install -o root -g root -m 0644 \
  udev/101-ftdi-access.rules /etc/udev/rules.d/101-ftdi-access.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

The user running TestbenchDAQ must belong to `plugdev`. Log out and back in
after changing group membership.

Verify the command:

```bash
tbdaq --help
```

`python -m tbdaq` and `python main.py` are equivalent entry points.

## Configure

Copy the safe template:

```bash
cp config_example.json config.json
```

`config.json` is machine-specific and intentionally excluded from Git. At
least one sensor must be enabled before a session can start.

Paths in a JSON configuration are resolved relative to that configuration
file. Unknown or misspelled configuration keys are rejected.

Hardware identity is optional:

- If `endaq.serial`, `endaq.model`, and `endaq.mount_path` are omitted,
  exactly one enDAQ must be discoverable.
- If identity is configured, the discovered recorder must match it.
Command-line values override JSON values.

Inspect the fully resolved configuration without commanding hardware:

```bash
tbdaq --config config.json show-config
```

Give a session a readable name in JSON or on the command line:

```bash
tbdaq --config config.json --name "bearing outer-race baseline"
```

The sanitized name prefixes the collision-resistant session directory and the
verbatim name is stored in the manifest.

Inspect the mounted enDAQ and its configurable channel IDs without starting a
recording or changing its configuration:

```bash
tbdaq --config config.json endaq-info
```

On the S3-E100D40, measurement range is selected through the channel: channel
8 is the 100 g PE accelerometer and channel 80 is the 40 g DC accelerometer.
Only settings present in `endaq.channels` are enforced. For example:

```json
"channels": {
  "8":  {"enabled": true,  "sample_rate_hz": 20000},
  "80": {"enabled": false}
}
```

`recording_time_limit_s` and `recording_size_limit_bytes` are optional. Omit
or use `null` to preserve the recorder's setting; use `0` to explicitly clear
an existing limit.

## Modes and run parameters

### Diagnostic

A diagnostic session is exactly one manual or timed run.

Timed:

```bash
tbdaq --config config.json \
  --mode diagnostic \
  --run-duration-s 30
```

Manual stop:

```bash
tbdaq --config config.json \
  --mode diagnostic \
  --manual
```

Press Enter to end a manual run.

### Prognostic

A prognostic session contains one or more timed runs. `run_period_s` is the
planned start-to-start period.

```bash
tbdaq --config config.json \
  --mode prognostic \
  --run-count 12 \
  --run-duration-s 60 \
  --run-period-s 300
```

If enDAQ cleanup overruns a planned start, `missed_start_policy` controls the
result. The default, `abort`, stops the session with a prominent schedule-
violation error. Set it to `start_late` only when deliberately accepting a
shifted schedule; the exact lateness is then recorded as a warning.

Every prognostic run stops the enDAQ, waits for remount, copies and verifies
its IDE, and only then permits the next scheduled run. IDE signal export is
deferred until all scheduled acquisitions finish, so processing cannot delay a
later start. Remount/offload time is scheduling overhead in addition to
`run_duration_s`; exact lateness is retained in the manifest.

### Common measurement window

For a timed combined run:

1. Enabled sensors are started concurrently.
2. The application waits until every required adapter reports ready.
3. The configured duration starts.
4. Stop is requested concurrently for all started sensors.

Sensor preparation time does not consume the requested measurement duration.
The manifest records command dispatch/return times and the common overlap
window separately.

## Strict and partial operation

Strict operation is the default:

```json
"allow_partial": false
```

If any enabled sensor is unavailable or fails to start, the run is aborted and
anything that did start is stopped.

For troubleshooting only, partial acquisition can be enabled:

```bash
tbdaq --config config.json --allow-partial
```

Partial acquisition is clearly marked `partial` in the manifest and process
exit status remains non-zero.

## Selecting sensors and parameters

Examples:

```bash
# Gator only
tbdaq --config config.json --gator --no-endaq --run-duration-s 10

# enDAQ only, requiring the expected recorder
tbdaq --config config.json --endaq --no-gator \
  --endaq-serial S0016418 \
  --endaq-model S3-E100D40 \
  --run-duration-s 30

# Combined run with Gator acquisition settings
tbdaq --config config.json \
  --gator-channel 8 \
  --gator-samplerate 1000 \
  --gator-fullscale 120 \
  --gator-threshold 0.2
```

Use `tbdaq --help` for every available override.

## Interruption and data retention

- Enter stops a manual diagnostic run.
- Ctrl+C during a timed run requests cleanup before the program exits.
- Started sensors are stopped concurrently.
- Partial files and manifests are retained.
- Every new IDE is copied after each run and verified by size and SHA-256.
- Multiple unexpected new IDE files are all preserved and flagged.
- `delete_after_verified_offload` defaults to `false`, retaining recorder-side
  files.
- For multi-day tests that exceed recorder capacity, explicitly set
  `delete_after_verified_offload` to `true`. Deletion occurs only after the
  host copy passes both size and SHA-256 verification, and every deletion is
  recorded in the manifest and log.
- At least `minimum_free_space_bytes` plus an estimated run allowance must be
  available before recording. The allowance includes the configured remount
  timeout because the enDAQ may continue recording while its serial stop
  interface becomes available. The default floor is 1 GiB.

If TestbenchDAQ reports that the enDAQ may already be recording, it refuses to
take ownership automatically and prints this operator command:

```bash
tbdaq --config config.json endaq-stop
```

That command sends stop to the configured serial number and waits for remount.
It does not attempt session offload recovery.

Conversion failure does not invalidate a checksum-verified raw acquisition.
The run remains successful with `processing_status: incomplete` and a warning
in the manifest.

TestbenchDAQ reads IDE files directly and writes final per-signal CSVs in one
pass. It no longer creates intermediate channel CSVs. On the Odroid, the
representative 3.64 MB `DAQ16418_000417.IDE` improved from 30.2 seconds and
54 MB of generated text to 22.7 seconds and 32 MB. CSV remains CPU-intensive:
hundreds of thousands of calibrated binary samples expand to more than a
million formatted text values.

Gator output always contains all eight `gator_sensor_N_fm.csv` files. An
all-zero sensor is preserved rather than silently omitted.

## Output

Each session uses a collision-resistant UTC identifier:

```text
csv-output/
  20260831T143012.482193Z-a1b2/
    session.log
    session_manifest.json
    signal_export_map.csv
    run_01/
      raw/
        gator/
        endaq/
      signals/
        gator/
        endaq/
```

The manifest is replaced atomically after state changes so interrupted
sessions retain useful status information.

The `signals` directory is intentionally not named `isa`: the current output
is not yet a complete ISA-PHM package.

## Convenience scripts

- `scripts/run_configured.sh`
- `scripts/run_single.sh`
- `scripts/run_continuous.sh`
- `scripts/run_gator_only.sh`
- `scripts/run_burst.sh`
- `scripts/run_soak.sh`

Timing, count, period, and sensor parameters come from `config.json`; scripts
no longer inject positional defaults. Named options are explicit overrides:

```bash
./scripts/run_burst.sh
./scripts/run_burst.sh --run-count 5 --name trial-a
TBDAQ_CONFIG=/path/to/test.json ./scripts/run_soak.sh
```

Normal output shows lifecycle milestones. Use `-q` for warnings/errors only,
`-qq` for errors only, or `-v` for debug output including native Gator logs.
On an interactive terminal, warnings are yellow and errors are red. A failed
session prints its concrete run/adapter errors after the summary; ANSI colors
are automatically omitted for redirected output or when `NO_COLOR` is set.

## Development

The orchestration tests use simulated adapters and do not command hardware:

```bash
python -m unittest discover -s tests -v
```

Compile-check the Python package:

```bash
python -m compileall -q tbdaq tests
```

## Current limitations

- Gator selection still uses the first device returned by the native library.
- Cross-device sample timestamps are not yet normalized to one shared
  reference.
- Full interrupted-session offload/conversion recovery is not implemented.
- Multi-day recorder-space reclamation requires explicitly enabling verified
  recorder-side deletion.
- Signal exports are not yet ISA-JSON or ISA-Tab.
- Hardware acceptance and endurance tests are still required.
