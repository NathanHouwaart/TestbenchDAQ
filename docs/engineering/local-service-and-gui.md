# Local service and GUI integration

The local service is the boundary between an operator-facing GUI and hardware
configuration on one acquisition host. It listens only on `127.0.0.1:8765`.

```text
Lager-Testbank GUI
        |
        | validate / start / complete / abort
        v
TestbenchDAQ local service
        |
        | machine.json + GUI acquisition request
        v
Gator, enDAQ, NFS session storage
```

## Ownership boundary

The service starts with a protected `machine.json`. A request may describe a
session name, schedule and source/channel settings, but it cannot change:

- `output_root`;
- machine identity;
- Gator binary, library, or device selection;
- enDAQ serial, model, mount path, or recorder lifecycle safeguards.

This is why the same DAQ library supports both usage modes:

- standalone CLI: operator explicitly supplies `--machine` and
  `--acquisition`;
- Lager-Testbank: the GUI owns the acquisition definition while the host owns
  the protected machine definition.

## Service API lifecycle

The GUI first submits its request to `POST /validate`. It then starts the
session with `POST /runs/start` before starting the PLC program. It polls
`GET /status` to display preparation, armed, recording, finalising, queued,
completed, aborted, or failed state.

`POST /runs/complete` means the PLC program reached its normal completion;
TestbenchDAQ finishes any active acquisition window and publishes processing
work when configured. `POST /runs/abort` stops the active acquisition when the
operator aborts the PLC program.

The service rejects a new start while a session is still active or finalising.
This prevents a new PLC test from colliding with recorder cleanup or data
offload.

## Storage and portal handoff

For NFS-backed sessions, TestbenchDAQ writes a processing request only after
all raw recorder files are closed and verified. The central processor then
creates derived signal CSV files. A local-output session processes locally and
does not depend on the central portal.

See [portal and shared storage](portal-and-storage.md) for deployment details.
