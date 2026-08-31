# Phase 2 design contract

## Safety defaults

- Sensor families are disabled unless explicitly enabled.
- Every enabled family is required unless `allow_partial` is true.
- A failed required start causes already-started sensors to be stopped.
- Recorder-side enDAQ files are retained unless verified deletion is explicitly
  configured.
- Invalid and unknown configuration values fail before hardware is commanded.

## Session states

A session progresses through:

```text
initializing -> preflight -> running -> success | partial | failed
                                      -> aborted | interrupted
```

Each run progresses through:

```text
starting -> measuring -> stopping -> processing
         -> success | partial | failed | interrupted
```

The JSON manifest is written atomically at session creation, after preflight,
after every run, and at terminal session status.

## Scheduling

Prognostic run starts are calculated from one monotonic base time. Completion
of the preceding run does not redefine the schedule. This prevents processing
time from silently shifting a test program.

If the next start is later than its configured tolerance, the default `abort`
policy stops the session with an explicit error. `start_late` is an opt-in
policy that records the lateness and continues immediately.

enDAQ stop, remount, offload, and checksum verification are part of every run.
Prognostic IDE signal export is deferred until scheduled acquisition has ended.
This separates mandatory recorder-space management from optional processing.

IDE signals are exported directly from one parsed/calibrated IDE document.
Final CSV formatting is chunked; intermediate combined channel CSVs are not
generated.

## enDAQ lifecycle

The enDAQ filesystem is flushed and cleanly unmounted before `RecStart`; this
prevents the recorder's USB mode switch from tearing down a mounted FAT
filesystem. The enDAQ library's start return value is not treated as
authoritative because its serial implementation can compare a stale response
status after sending the command. Disappearance of the previously mounted USB
block device proves recording started.

Stop retries the configured recorder's serial interface for up to
`remount_timeout_s`, then rediscovers the mounted recorder by identity. A start
command with uncertain outcome is always included in cleanup.

The Linux mount contract uses one mechanism: a udev `SYSTEMD_WANTS` dependency
starts the `/etc/fstab`-generated `mnt-endaq.mount` unit when the UUID appears.
There is no automount and no timer-coalesced `systemd-run` command.

Before each run, TestbenchDAQ snapshots recorder filenames and storage
capacity. After remount, it accepts only newly appearing IDE filenames, copies
all of them, and verifies size and SHA-256. It never silently substitutes an
older "latest" file.

The standalone `endaq-stop` action is intentionally limited to stopping and
remounting an already-recording configured device. It is not a general session
recovery mechanism.

## Gator lifecycle

GTRLib v0.1.0 provides asynchronous `subscribe()` but no matching unsubscribe
or disconnect operation. Its teardown can intermittently block after capture
and CSV flushing have completed. The native recorder therefore explicitly
closes the CSV, flushes its completion token, and exits without invoking the
vendor-owned destructors. Process termination releases the remaining USB
handles.

## Time terminology

Phase 1 records host UTC for:

- adapter command dispatch;
- adapter start-call return;
- start of the common measurement window;
- stop request;
- adapter stop-call return.

These values describe orchestration and are not claimed to prove sample-level
synchronization. Processed enDAQ channels are cropped against this host window
and share its start as `t=0`. Gator output is duration-cropped and uses its first
retained sample as `t=0`, because its observed device UTC offset is not trusted.
Native sample rates are preserved and no resampling, phase correction, or clock
drift correction is applied. Every run records this as
`synchronization.method = "common_window_only"` in the manifest.

## Exit statuses

- `0`: all runs completed successfully;
- `1`: partial, failed, aborted, or interrupted session;
- `2`: invalid configuration;
- `130`: interruption before a session manifest can handle the event.
