# Deployment overview

TestbenchDAQ has three deployable roles:

| Role | Runs on | Responsibility |
| --- | --- | --- |
| Acquisition host | Odroid/Raspberry Pi beside a bench | Hardware access, local DAQ service, NFS write access. |
| Storage/portal host | ML server | NFS exports, read-only portal, conversion worker. |
| Lager-Testbank GUI | Operator workstation or acquisition host | PLC coordination and acquisition-request generation. |

The detailed server/NFS/portal runbook is [portal and shared storage](portal-and-storage.md).
The service/GUI contract is [local service and GUI integration](local-service-and-gui.md).

## Acquisition-host service

The Lager-Testbank checkout installs the localhost service from its `deploy/`
directory. It stores the protected local machine configuration at:

```text
<Lager-Testbank checkout>/config/testbenchdaq-machine.json
```

The service runs the DAQ submodule and starts it with `--machine`; it never
uses a legacy combined configuration file. See the Lager-Testbank deployment
guide for the exact installation command used on that checkout.
