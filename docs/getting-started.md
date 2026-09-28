# Getting started with TestbenchDAQ

This guide sets up a Linux acquisition host, confirms the command is available,
and records a short diagnostic measurement. The host is physically connected to
the Gator, enDAQ, or both; it is not the PLC/controller that produces the
test-bench operating-condition CSV files.

For the system context and the later ISA-PHM-wizard handoff, see the
[README](../README.md). For shared experiment storage and the data portal, see
[central storage and data portal](data-portal.md).

For copyable diagnostic and prognostic command examples, see the
[command cookbook](command-cookbook.md).

## 1. Check the host

Production acquisition is supported on Linux/AArch64 with Python 3.11 or
newer. Connect only the hardware you plan to use.

Gator support additionally requires access to MaintenanceLab's private
PhotonFirst runtime release. The runtime has separate distribution terms and
is not stored in this repository. Authenticate GitHub CLI with an authorized
account before choosing Gator setup:

```bash
gh auth login
```

Windows is currently limited to the experimental Gator helper described in
[gator_recorder/windows/README.md](../gator_recorder/windows/README.md).

## 2. Clone and install

```bash
git clone https://github.com/NathanHouwaart/TestbenchDAQ.git
cd TestbenchDAQ
./scripts/install/setup_linux.sh
```

The guided installer creates `.venv`, installs TestbenchDAQ, then asks whether
to install Gator and enDAQ support. It finishes by running `tbdaq --help`.

To choose components without prompts:

```bash
# Gator runtime plus USB access rule
./scripts/install/setup_linux.sh --gator --non-interactive

# deterministic enDAQ mounting
./scripts/install/setup_linux.sh --endaq --non-interactive

# both components
./scripts/install/setup_linux.sh --all --non-interactive
```

The component installers can also be run directly:

```bash
./scripts/install/install_gator.sh
./scripts/install/install_endaq.sh
```

`install_gator.sh` installs the native recorder, the approved runtime, and the
Gator USB rule. If it adds your account to `plugdev`, log out and back in, then
reconnect the Gator before recording.

`install_endaq.sh` installs the deterministic mount rule and performs a
read-only FAT check. It backs up the relevant `/etc/fstab` and udev files
before changing them. If the FAT check reports an error, it leaves the device
unmounted and does not repair its filesystem.

## 3. Create and inspect your configuration

```bash
cp config_example.json config.json
.venv/bin/tbdaq --config config.json show-config
```

`config.json` is local to the machine and intentionally excluded from Git.
Enable `gator`, `endaq`, or both there—or use the explicit command-line flags
in the next step. If several Gators or enDAQ recorders are attached, configure
their identities before an unattended experiment.

### Choose how to supply settings

You can use either approach, or combine them:

- **Configuration-first:** put normal machine, sensor, and experiment settings
  in `config.json`, then run a short command such as
  `.venv/bin/tbdaq --config config.json`. This is the preferred approach for
  repeatable experiments.
- **Command-line-first:** provide sensor and run settings directly, without a
  configuration file. This is useful for a quick, explicit hardware check.
- **Configuration plus overrides:** load `config.json` and add a command-line
  value for a one-off change. Only the supplied option overrides that matching
  configuration value.

For example, this uses the configuration except for one Gator sample-rate
override:

```bash
.venv/bin/tbdaq --config config.json --gator-samplerate 5000
```

This is the equivalent style with no configuration file: every required choice
is explicit on the command line.

```bash
.venv/bin/tbdaq --mode diagnostic --gator --no-endaq \
  --gator-channel 1 --gator-samplerate 5000 --gator-fullscale 9 \
  --run-duration-s 10 --output-root ./csv-output --allow-local-output
```

For enDAQ, inspect the mounted recorder and its channel IDs without recording:

```bash
.venv/bin/tbdaq --config config.json endaq-info
```

## 4. Record a first local diagnostic run

These examples deliberately use local storage. `--allow-local-output` makes
that exception explicit and records `local_override` in the manifest. Use NFS
storage for real/shared experiments, as described in the
[data-portal guide](data-portal.md).

Choose the command that matches the connected hardware. More local and NFS
examples are in the [command cookbook](command-cookbook.md).

```bash
# Gator only
.venv/bin/tbdaq --config config.json --gator --no-endaq \
  --output-root ./csv-output --allow-local-output --run-duration-s 10

# enDAQ only
.venv/bin/tbdaq --config config.json --endaq --no-gator \
  --output-root ./csv-output --allow-local-output --run-duration-s 10

# Gator and enDAQ together
.venv/bin/tbdaq --config config.json --gator --endaq \
  --output-root ./csv-output --allow-local-output --run-duration-s 10
```

Each command is a diagnostic test: one timed measurement. The resulting
session directory contains raw acquisitions, per-sensor signal CSV files, a
session manifest, and a log. The manifest documents orchestration timing, but
does not establish sample-level synchronization between Gator and enDAQ.

## 5. Move to normal experiment storage

For a shared or production experiment, mount writable NFS/NFSv4 storage on the
acquisition host and set its mount path as `output_root` in `config.json`.
TestbenchDAQ refuses a normal run when that location is not actually a writable
network mount, preventing accidental use of local disk.

For scheduled run-to-failure (prognostic) acquisition, configure a run count,
duration, and start-to-start period. The period must include enDAQ stop,
remount, and verified offload time:

```bash
.venv/bin/tbdaq --config config.json --mode prognostic \
  --run-count 12 --run-duration-s 60 --run-period-s 300
```

The PLC/controller's operating-condition CSV files remain a separate input
today. Later, the ISA-PHM wizard combines them with TestbenchDAQ output and
experiment metadata into the complete documented dataset.

## Run helpers

The `scripts/run/` helpers use `config.json` by default; set `TBDAQ_CONFIG` to
choose another configuration file.

```bash
./scripts/run/single.sh
./scripts/run/gator_only.sh
./scripts/run/burst.sh --run-count 5 --name trial-a
TBDAQ_CONFIG=/path/to/test.json ./scripts/run/soak.sh
```

Use `./scripts/run/configured.sh --help` for the full command-line interface.
For a manual diagnostic measurement, use `./scripts/run/continuous.sh` and
press Enter to stop. Press Ctrl+C once to cleanly stop a timed run; do not
disconnect devices while enDAQ is stopping or remounting.
