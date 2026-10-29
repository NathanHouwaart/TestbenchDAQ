# Acquisition-session export

Each completed TestbenchDAQ session writes two audiences of metadata at the
session root:

- `session_manifest.json` is the operational record used by the recorder
  service and portal.
- `acquisition-session.json` is the public, machine-neutral contract consumed
  by downstream tools such as the ISA-PHM Wizard.

The public manifest uses `window_001`, `window_002`, and so on. A window is a
time-bounded technical capture. It is not an ISA-PHM Study or Run; the Wizard
maps windows to diagnostics Studies or prognostic trajectories later.

`acquisition_window_map.csv` lists exported signals using relative paths only.
Absolute paths remain internal implementation details and must not be used by
importers. The versioned JSON Schema lives in the ISA-PHM Wizard repository at
`contracts/acquisition-session/v1/`.
