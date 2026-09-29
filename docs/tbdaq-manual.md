# tbdaq manual

`tbdaq` coordinates Gator and enDAQ acquisition.

```text
tbdaq [OPTIONS] [run | show-config | endaq-info | endaq-stop]
```

`run` is the default. You may use `config.json`, command-line options, or both. A supplied command-line value overrides only the matching JSON setting; this makes `config.json` a repeatable baseline with one-off overrides. Relative paths resolve from the configuration file. `show-config` prints resolved settings without hardware. `endaq-info` describes an enabled enDAQ. `endaq-stop` stops/remounts an enabled enDAQ after interruption.

## General options

| Option | Meaning |
| --- | --- |
| `-h`, `--help` | Show usage and exit. |
| `--config PATH` | Read JSON configuration from `PATH`. |
| `--name NAME` | Session prefix and manifest name: 1–80 characters, starting with a letter/number; following characters may be letters, numbers, spaces, `.`, `_`, or `-`. |
| `--machine-name NAME` | Manifest host identity: lowercase hostname-style letters, digits, and hyphens. |
| `--output-root DIR` | Session-output directory; normal acquisition requires writable NFS/NFSv4 storage. |
| `--allow-local-output` | Bypass NFS check for this run, marking the manifest `local_override`; use for setup/development only. |
| `-v`, `--verbose` | Debug output including native Gator messages; mutually exclusive with `-q`. |
| `-q`, `--quiet` | Less output: `-q` warnings/errors, `-qq` errors only; mutually exclusive with `-v`. |

## Mode and run options

| Option | Meaning |
| --- | --- |
| `--mode diagnostic` | Exactly one manual or timed measurement. |
| `--mode prognostic` | Repeated timed run-to-failure/degradation measurements; requires duration and period. |
| `--run-count N` | Exactly `N` measurements (`N >= 1`); diagnostic requires `1`; mutually exclusive with `--run-until-stopped`. |
| `--run-until-stopped` | Repeat prognostic runs until Ctrl+C and clean up active run; mutually exclusive with `--run-count`. |
| `--run-duration-s SECONDS` | Timed duration (`> 0`); mutually exclusive with `--manual`; required for prognostic mode. |
| `--manual` | Diagnostic run until Enter; mutually exclusive with duration; unavailable for prognostic mode. |
| `--run-period-s SECONDS` | Prognostic start-to-start period; include duration plus startup/shutdown and enDAQ stop/remount/offload overhead. Gator and enDAQ signal export is deferred until the series ends. |
| `--missed-start-tolerance-s SECONDS` | Permitted schedule lateness (`>= 0`). |
| `--missed-start-policy abort` | Stop when a prognostic start is too late. Default. |
| `--missed-start-policy start_late` | Start overdue run immediately and log lateness. |
| `--allow-partial` | Continue if enabled sensor is unavailable/fails; result is `partial` and non-zero. |
| `--no-allow-partial` | Require every enabled sensor to start. Default. |

## Gator options

`--gator` and `--no-gator` enable or disable Gator for this command.

| Option | Meaning |
| --- | --- |
| `--gator`, `--no-gator` | Enable or disable Gator acquisition. |
| `--gator-binary PATH` | Native recorder executable; normal installation uses `/usr/local/bin/gator_recorder`. |
| `--gator-library DIR` | PhotonFirst shared-library directory for a custom installation. |
| `--gator-device-index INDEX` | Zero-based selection when several Gators are connected; one detected Gator is automatic. |
| `--gator-channel 1-8` | Measurement channel. |
| `--gator-samplerate HZ` | `1000`, `5000`, `10000`, or `19000` Hz; omit to retain device setting. |
| `--gator-fullscale 8-127` | Full-scale range; omit to retain device setting. |
| `--gator-threshold 0-1` | Detection threshold; omit to retain device setting. |
| `--gator-start-timeout-s SECONDS` | Startup wait limit (`> 0`). |
| `--gator-stop-timeout-s SECONDS` | Stop wait limit (`> 0`). |

## enDAQ options

`--endaq` and `--no-endaq` enable or disable enDAQ for this command.

| Option | Meaning |
| --- | --- |
| `--endaq`, `--no-endaq` | Enable or disable enDAQ acquisition. |
| `--endaq-serial SERIAL` | Require a recorder serial-number match. |
| `--endaq-model MODEL` | Require a recorder model match. |
| `--endaq-mount-path PATH` | Recorder mount point; omit for discovery. |
| `--endaq-ide-converter COMMAND_OR_PATH` | Deprecated; direct IDE export is built in. |
| `--endaq-command-timeout-s SECONDS` | Control-command timeout (`> 0`). |
| `--endaq-remount-timeout-s SECONDS` | Wait after stop for storage remount (`> 0`). |
| `--endaq-minimum-free-space-bytes BYTES` | Minimum host free space (`>= 0`); default 1 GiB. |
| `--endaq-estimated-bytes-per-second BYTES` | Expected rate for storage preflight (`> 0` when set). |
| `--endaq-recording-time-limit-s SECONDS` | Recorder time limit; `0` clears it, omission preserves it. |
| `--endaq-recording-size-limit-bytes BYTES` | Recorder size limit; `0` clears it, omission preserves it. |
| `--endaq-delete-after-verified-offload` | Delete recorder IDE only after host-copy size/SHA-256 verification. |
| `--no-endaq-delete-after-verified-offload` | Retain recorder IDE after verified offload. Default. |

## JSON-only settings and exit status

`endaq.channels` accepts either the legacy ID-keyed object or a readable list of
channel objects. List entries require `id`; `name`, `subchannels`,
`sample_rate_min_hz`, `sample_rate_max_hz`, and
`supported_sample_rates_hz` are informational, while only `enabled` and
`sample_rate_hz` are enforced. The example configuration includes the complete
S3-E100D40 profile, so operators do not need to guess channel IDs or valid
rates. Inspect another recorder before using this profile on it:

```bash
tbdaq --config config.json endaq-info
```

Each channel returned by `endaq-info` includes its configured
`sample_rate_hz`, recorder-provided `sample_rate_min_hz` and
`sample_rate_max_hz`, plus `supported_sample_rates_hz` when the recorder
provides a discrete rate list. `sample_rate_controls` lists every matching
ConfigUI control and identifies whether it belongs to the parent channel or a
subchannel. `null` parent bounds mean only that no parent-channel control was
found; inspect `sample_rate_controls` before concluding that a channel has no
configurable rate.

| Status | Meaning |
| --- | --- |
| `0` | Successful session or inspection. |
| `1` | Partial, failed, aborted, interrupted, or runtime-failed session. |
| `2` | Invalid configuration. |
| `130` | Interrupted before manifest handling. |
