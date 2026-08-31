# Phase 1 design contract

## Safety defaults

- Sensor families are disabled unless explicitly enabled.
- Every enabled family is required unless `allow_partial` is true.
- A failed required start causes already-started sensors to be stopped.
- Recorder-side enDAQ files are retained.
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

If the next start is later than its configured tolerance, the session aborts.

## Time terminology

Phase 1 records host UTC for:

- adapter command dispatch;
- adapter start-call return;
- start of the common measurement window;
- stop request;
- adapter stop-call return.

These values describe orchestration and are not claimed to be device sample
timestamps. Sample-level clock normalization is a separate synchronization
phase.

## Exit statuses

- `0`: all runs completed successfully;
- `1`: partial, failed, aborted, or interrupted session;
- `2`: invalid configuration;
- `130`: interruption before a session manifest can handle the event.
