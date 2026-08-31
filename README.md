# TestbenchDAQ

TestbenchDAQ coordinates a PhotonFirst Gator FBG interrogator and an enDAQ
recorder from one Linux command-line application.

The current Phase 1 release establishes safe configuration, scheduling,
failure handling, cleanup, manifests, and testable adapter boundaries. Signal
CSV files are currently an interim representation. Complete cross-device time
normalization and ISA-PHM metadata serialization are subsequent phases.

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
file. Bare executable names such as `ideexport` are resolved through `PATH`.
Unknown or misspelled configuration keys are rejected.

Hardware identity is optional:

- If `endaq.serial`, `endaq.model`, and `endaq.mount_path` are omitted,
  exactly one enDAQ must be discoverable.
- If identity is configured, the discovered recorder must match it.
Command-line values override JSON values.

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

If a run misses its planned start by more than
`missed_start_tolerance_s`, the session aborts instead of silently starting
late.

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
- An offloaded IDE is verified by size and SHA-256.
- The recorder-side IDE is retained; TestbenchDAQ does not delete it.

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
      converted/
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

- `scripts/run_single.sh`
- `scripts/run_continuous.sh`
- `scripts/run_gator_only.sh`
- `scripts/run_burst.sh`
- `scripts/run_soak.sh`

All scripts use `config.json` and accept additional command-line overrides.

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
- enDAQ conversion is still a post-recording step.
- Signal exports are not yet ISA-JSON or ISA-Tab.
- Hardware acceptance and endurance tests are still required.
