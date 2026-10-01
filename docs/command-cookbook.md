# TestbenchDAQ command cookbook

Every command combines a local machine installation file with a portable
acquisition file. Create them once from the tracked examples:

```bash
cp machine.example.json machine.json
cp acquisition.example.json acquisition.json
```

Edit `machine.json` for the host and use a separate acquisition file for each
measurement. Configuration is intentionally not overridden by command-line
flags: the file used for the run is the complete, reviewable record.

```bash
DAQ='.venv/bin/tbdaq --machine machine.json --acquisition acquisition.json'
```

## Inspect before recording

```bash
$DAQ show-config
$DAQ endaq-info
```

`endaq-info` requires `endaq.enabled: true` in `acquisition.json`.

## Local diagnostic check

Set a local output directory in a temporary `machine.json`, choose a
single diagnostic window and enabled sources in `acquisition.json`, then:

```bash
$DAQ --allow-local-output
```

The manifest records that local-output exception. Do not use it for shared
experiments.

## Normal NFS experiment

Set `output_root` in `machine.json` to a writable NFS/NFSv4 mount and define
the schedule and sensors in `acquisition.json`:

```bash
$DAQ
```

For repeated windows, set `run_count`, `window_duration_s`, and
`run_period_s` in `acquisition.json`. `run_period_s` is start-to-start; it
must include the recording duration and all Gator/enDAQ cleanup. For enDAQ,
the Lager-Testbank integration additionally enforces its recorder recovery
time before the next start command.

## enDAQ recovery after interruption

With the same machine and acquisition files:

```bash
$DAQ endaq-stop
```

This stops a recorder that may still be recording and waits for its filesystem
to remount. Do not disconnect the recorder during this operation.
