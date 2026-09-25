# TestbenchDAQ user manual

TestbenchDAQ coordinates a PhotonFirst Gator FBG interrogator and an enDAQ recorder from one command-line program. It runs on the Linux computer physically connected to the instruments. The Gator vendor runtime is supplied for Linux/AArch64, so Windows and macOS are not supported.

## Before you start

You need Python 3.11+, access to MaintenanceLab's private PhotonFirst runtime release, and (when using enDAQ) the recorder connected by USB. The vendor GTR library stays outside this public repository because it has separate distribution terms.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
gh auth login
./scripts/install_gator_linux.sh
```

The `gh auth login` account needs read access to MaintenanceLab's private
`ML-Machine-Vendor-Runtimes` repository. The installer downloads the pinned,
checksum-verified Linux runtime release, builds the recorder, installs it to
`/usr/local/bin`, installs the PhotonFirst shared library to
`/usr/local/lib/photonfirst`, and registers it with the Linux linker. No
`binary_path` or `library_path` is needed in an operator command or
configuration after this succeeds.

Follow the README's **Install** section for USB permissions and enDAQ mount setup, then verify `tbdaq --help`.

## Configure a bench

```bash
cp config_example.json config.json
tbdaq --config config.json show-config
```

Enable `gator`, `endaq`, or both. The Gator installer supplies the standard
binary and library locations automatically. Only set `binary_path` or
`library_path` when deliberately using a non-standard custom installation.
For enDAQ, optionally pin `serial`, `model`, and `mount_path`; otherwise
exactly one discovered recorder is required. Inspect its configurable channels
with `tbdaq --config config.json endaq-info`.

Relative paths are resolved relative to `config.json`. Check their effective absolute values using `show-config` before an unattended test.

## Run a measurement

A diagnostic run is exactly one measurement:

```bash
tbdaq --config config.json --mode diagnostic --run-duration-s 30
tbdaq --config config.json --mode diagnostic --manual
```

A prognostic session repeats a schedule. `run_period_s` is start-to-start, so it must include enough time for enDAQ stop, remount, and verified offload.

```bash
tbdaq --config config.json --mode prognostic \
  --run-count 12 --run-duration-s 60 --run-period-s 300
```

For unlimited timed prognostic acquisition, set `"run_count": null` in the configuration or use:

```bash
tbdaq --config config.json --mode prognostic --run-until-stopped \
  --run-duration-s 60 --run-period-s 300
```

Press Ctrl+C once to finish the active run cleanly. Its raw data and manifest are retained and the session status becomes `interrupted`. If enDAQ stops and its IDE is verified on the output storage, TestbenchDAQ also completes its CSV conversion before exiting. Do not kill the process or remove USB devices while it is stopping an enDAQ.

### Worked example: a 30-run Gator test on `wentelteef`

After the NFS mount has been configured, `wentelteef` writes directly to the
server through `/mnt/testbench-results`. The following test captures Gator
channel 1 at 5 kHz, uses full scale 9, records each run for 120 seconds, and
starts runs every 240 seconds. It has 30 runs, so its planned duration is just
under two hours, plus any acquisition cleanup overhead.

Run this on **wentelteef** from the repository directory:

```bash
python3 main.py \
  --machine-name wentelteef \
  --output-root /mnt/testbench-results \
  --gator \
  --gator-channel 1 \
  --gator-fullscale 9 \
  --gator-samplerate 5000 \
  --mode prognostic \
  --run-count 30 \
  --run-duration-s 120 \
  --run-period-s 240 \
  --name test-skf6204-22-09-2026
```

Do **not** add `--allow-local-output` for this normal server-backed run. The
program first verifies that `/mnt/testbench-results` is writable NFS storage,
then creates the session directory below the `wentelteef` export on the server.

The session name must begin with a letter or number. In particular,
`--name -test-skf6204-22-09-2026` is invalid because the leading `-` looks like
another command-line option and is not allowed in a session name.

If this should run indefinitely rather than exactly 30 times, replace:

```text
--run-count 30
```

with:

```text
--run-until-stopped
```

`--run-count` and `--run-until-stopped` cannot be used together. Stop an
unlimited run with Ctrl+C; TestbenchDAQ cleans up the active run before exiting.

## Store data elsewhere

Set `output_root` to a writable NFS/NFSv4 mount. For a server on the same subnet, mount its NFS share on the Linux acquisition host first, then make that mount the output root:

```json
{
  "output_root": "/mnt/testbench-results"
}
```

For NFS, an administrator can mount an exported directory (replace placeholders):

```bash
sudo mkdir -p /mnt/testbench-results
sudo mount -t nfs server.example:/exports/testbench /mnt/testbench-results
```

Before a real run, ensure the share is mounted and writable as the same user running TestbenchDAQ. A disconnected network share causes the program to refuse to start rather than accidentally filling local storage. For a deliberately local exception, provide `--allow-local-output` on the command line; the resulting manifest is marked `local_override`.

## Find results and recover safely

Each unique session directory contains `session_manifest.json` (status and configuration), `session.log`, `run_XX/raw/` (raw files), `run_XX/signals/` (CSV exports), and `signal_export_map.csv`. Create an offline plot with:

```bash
.venv/bin/python scripts/plot_session.py /path/to/SESSION_ID
```

If enDAQ may still be recording after an interruption, use `tbdaq --config config.json endaq-stop` and wait for it to remount. Keep `delete_after_verified_offload` disabled until host storage and the verified-offload workflow have been validated.

## Publishing on GitHub

Review `git status` before publishing. Keep `config.json`, acquired data, vendor libraries, and build products out of Git, choose a license you are authorized to apply, and document that the Gator runtime is an external Linux-only prerequisite. The existing `.gitignore` already excludes the local config, output directory, virtual environments, and native build directory.
