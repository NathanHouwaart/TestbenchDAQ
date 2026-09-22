# Central storage and read-only data portal

This deployment keeps instrument control on each acquisition machine. The
server at `192.168.0.189` stores data and hosts a browser-only portal; it has
no endpoint that can start, stop, or configure a Gator or enDAQ.

## NFS storage

On the server, install the NFS service and create a separate export for each
machine:

```bash
sudo apt install nfs-kernel-server
sudo mkdir -p /srv/testbenchdaq/wentelteef /srv/testbenchdaq/knarskast
sudo chown 1000:1000 /srv/testbenchdaq/wentelteef /srv/testbenchdaq/knarskast
```

Add the acquisition machines' fixed IP addresses to `/etc/exports` (replace
the examples):

```text
/srv/testbenchdaq/wentelteef 192.168.0.50(rw,sync,no_subtree_check)
/srv/testbenchdaq/knarskast 192.168.0.51(rw,sync,no_subtree_check)
```

Apply the exports with `sudo exportfs -ra`. On each acquisition machine,
install `nfs-common`, create `/mnt/testbench-results`, and mount its own export.
Persist the mount in `/etc/fstab` with `_netdev`; do not use `nofail`, because
a missing mount must prevent a measurement from starting.

```text
192.168.0.189:/srv/testbenchdaq/wentelteef /mnt/testbench-results nfs4 rw,hard,_netdev,x-systemd.requires=network-online.target 0 0
```

Each machine uses a matching configuration:

```json
{
  "machine_name": "wentelteef",
  "output_root": "/mnt/testbench-results"
}
```

Before hardware is commanded, TestbenchDAQ verifies that `output_root` is
writable NFS/NFSv4 storage. For a deliberately local run, an operator may use
`--allow-local-output`; the manifest prominently records that override. The
option is never persisted in `config.json`.

## Run the portal with Docker Compose

Install Docker Engine and the Docker Compose plugin on the server. The portal
reads `/srv/testbenchdaq` through a read-only container volume. NFS remains a
host service; do not run the NFS server in this Compose project.

Create a password file outside Git:

```bash
mkdir -p portal/auth
docker run --rm --entrypoint htpasswd httpd:2.4-alpine -Bbn viewer 'choose-a-strong-password' > portal/auth/.htpasswd
```

Build and start the portal from the repository root:

```bash
docker compose -f docker-compose.portal.yml up -d --build
```

It serves these LAN URLs after authentication:

- `http://192.168.0.189/wentelteef/machine-data/`
- `http://192.168.0.189/knarskast/machine-data/`

The React frontend refreshes manifest status every five seconds. The API and
containers expose only `GET` endpoints. Session files are served only after
their paths have been checked to remain inside that machine's session folder.

To update after pulling a new version, rerun the `docker compose ... up -d
--build` command. Check the service logs with:

```bash
docker compose -f docker-compose.portal.yml logs -f
```
