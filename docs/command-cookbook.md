# TestbenchDAQ command cookbook

These commands assume you are in the repository root, have completed setup,
and have copied `config_example.json` to `config.json`.

```bash
source .venv/bin/activate
cp config_example.json config.json  # only if config.json does not exist yet
```

Command-line options override `config.json`. The examples use explicit sensor
flags so that a short hardware check does not depend on the template's disabled
sensor settings.

There are three valid ways to run TestbenchDAQ: use `config.json` as the
repeatable baseline; provide every needed setting directly on the command line
for a quick check; or load `config.json` and override only a specific value for
one run. A supplied command-line option wins only for its matching setting.

## Local storage: short diagnostic checks

Local output is useful for setup and development. It must be made explicit with
`--allow-local-output`; the resulting session manifest is marked
`local_override`. Do not use it for a normal shared experiment.

```bash
# Gator only: channel 1, 5 kHz, full scale 9, 10-second diagnostic measurement
.venv/bin/tbdaq --config config.json --mode diagnostic \
  --gator --no-endaq --gator-channel 1 --gator-samplerate 5000 \
  --gator-fullscale 9 --run-duration-s 10 \
  --output-root ./csv-output --allow-local-output

# enDAQ only: 10-second diagnostic measurement
.venv/bin/tbdaq --config config.json --mode diagnostic \
  --endaq --no-gator --run-duration-s 10 \
  --output-root ./csv-output --allow-local-output

# Both external sensor systems: 10-second diagnostic measurement
.venv/bin/tbdaq --config config.json --mode diagnostic \
  --gator --endaq --gator-channel 8 --gator-samplerate 10000 \
  --gator-fullscale 9 --run-duration-s 10 \
  --output-root ./csv-output --allow-local-output
```

For a manual diagnostic test, use `--manual` instead of
`--run-duration-s`. Press Enter once to finish the measurement cleanly.

```bash
.venv/bin/tbdaq --config config.json --mode diagnostic --manual \
  --gator --no-endaq --gator-channel 1 --gator-samplerate 5000 \
  --gator-fullscale 9 --output-root ./csv-output --allow-local-output
```

Gator channels are `1` through `8`. Supported sample rates are `1000`,
`5000`, `10000`, and `19000` Hz; full scale is an integer from `8` through
`127`. Set a detection threshold from `0` to `1` only when you intend to
override the device setting, for example `--gator-threshold 0.2`.

## NFS server storage: normal experiments

The current deployment uses **NFS/NFSv4**, not NTFS, for shared acquisition
storage. Configure and mount the NFS share first using the
[data-portal guide](data-portal.md). Confirm that the acquisition host sees a
writable network mount before starting a real test:

```bash
mountpoint /mnt/testbench-results
findmnt -T /mnt/testbench-results -o TARGET,SOURCE,FSTYPE,OPTIONS
touch /mnt/testbench-results/.tbdaq-write-check && \
  rm /mnt/testbench-results/.tbdaq-write-check
```

Set the mount point in `config.json`:

```json
{
  "machine_name": "wentelteef",
  "output_root": "/mnt/testbench-results"
}
```

Then run a diagnostic measurement without `--allow-local-output`:

```bash
.venv/bin/tbdaq --config config.json --mode diagnostic \
  --gator --endaq --gator-channel 1 --gator-samplerate 5000 \
  --gator-fullscale 9 --run-duration-s 30 \
  --name "bearing baseline"
```

## NFS server storage: prognostic run-to-failure series

A prognostic session repeats timed measurements. `run_period_s` is measured
from one run start to the next, so it must allow for the measurement duration,
startup/shutdown, and enDAQ stop, remount, and verified offload time. Gator and
enDAQ signal CSV export is deferred until the scheduled series has ended.

```bash
# 30 runs, each 120 seconds, scheduled every 240 seconds
.venv/bin/tbdaq --config config.json --mode prognostic \
  --gator --endaq --gator-channel 1 --gator-samplerate 5000 \
  --gator-fullscale 9 --run-count 30 \
  --run-duration-s 120 --run-period-s 240 \
  --name "bearing-run-to-failure"
```

For an unlimited series, replace `--run-count` with `--run-until-stopped`.
Press Ctrl+C once to cleanly finish the active run and retain its files.

```bash
.venv/bin/tbdaq --config config.json --mode prognostic \
  --gator --endaq --gator-channel 1 --gator-samplerate 5000 \
  --gator-fullscale 9 --run-until-stopped \
  --run-duration-s 60 --run-period-s 300 \
  --name "bearing-run-to-failure"
```

## Useful inspection and recovery commands

```bash
# Resolve configuration paths and effective settings without commanding hardware
.venv/bin/tbdaq --config config.json show-config

# Inspect the mounted enDAQ and available channel IDs
.venv/bin/tbdaq --config config.json endaq-info

# Stop an enDAQ that may still be recording after an interruption, then wait for remount
.venv/bin/tbdaq --config config.json endaq-stop
```

TestbenchDAQ output and the PLC/controller's operating-condition CSV files are
still separate inputs. The ISA-PHM wizard later combines them with experiment
metadata into the complete documented dataset.
