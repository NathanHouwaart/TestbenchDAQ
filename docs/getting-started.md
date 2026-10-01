# Getting started with TestbenchDAQ

TestbenchDAQ runs on the Linux acquisition host connected to a Gator, enDAQ,
or both. It does not control the PLC. The command always combines two JSON
files:

- `machine.json` is local to one installed host: its identity, storage path,
  installed Gator runtime, and mounted enDAQ.
- `acquisition.json` is portable: a named measurement schedule and the sensor
  choices/rates used for that measurement.

This split is deliberate. Never copy hardware paths, recorder serial numbers,
or output roots into an experiment file.

## Install

```bash
git clone https://github.com/NathanHouwaart/TestbenchDAQ.git
cd TestbenchDAQ
./scripts/install/setup_linux.sh --all
```

`install_endaq.sh` installs deterministic recorder mounting and performs a
read-only FAT check. If it offers a repair, only accept after confirming the
recorder is stopped.

## Create the two files

```bash
cp machine.example.json machine.json
cp acquisition.example.json acquisition.json
```

Edit `machine.json` once for this host. It is intentionally ignored by Git.
Set `machine_name`, the NFS `output_root`, and the installed device identity
where applicable. Edit or generate `acquisition.json` for each measurement:
name, windows, Gator settings, and explicit enDAQ channel selection.

The acquisition example lists every known S3-E100D40 channel with
`enabled: false`. Enable only the channels used for that measurement. Its
sample-rate annotations are informational; TestbenchDAQ validates selected
settings against the attached recorder.

Inspect the resolved configuration without commanding hardware:

```bash
.venv/bin/tbdaq --machine machine.json --acquisition acquisition.json show-config
```

## First measurement

For a deliberate local setup run, set a local `output_root` in `machine.json`
and pass the explicit exception:

```bash
.venv/bin/tbdaq --machine machine.json --acquisition acquisition.json \
  --allow-local-output
```

For a normal experiment, `machine.json` must point at writable NFS/NFSv4
storage. No local-output flag is needed:

```bash
.venv/bin/tbdaq --machine machine.json --acquisition acquisition.json
```

To inspect or recover an enDAQ, set `endaq.enabled` to `true` in the selected
acquisition file:

```bash
.venv/bin/tbdaq --machine machine.json --acquisition acquisition.json endaq-info
.venv/bin/tbdaq --machine machine.json --acquisition acquisition.json endaq-stop
```

## Helpers

`scripts/run/configured.sh` uses `machine.json` and `acquisition.json` from
the repository root. For another pair, set both paths explicitly:

```bash
TBDAQ_MACHINE=/path/to/machine.json \
TBDAQ_ACQUISITION=/path/to/acquisition.json \
  ./scripts/run/configured.sh
```

The old `single`, `burst`, `soak`, `gator_only`, and `continuous` helpers are
compatibility aliases. They no longer override a measurement: make that choice
in `acquisition.json` so a run is reproducible and portable.

For NFS setup and the read-only portal, see [central storage and data portal](data-portal.md).
