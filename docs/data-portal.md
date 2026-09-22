# Central storage and read-only data portal

This deployment keeps instrument control on each acquisition machine. The
server at `192.168.0.189` stores data and hosts a browser-only portal; it has
no endpoint that can start, stop, or configure a Gator or enDAQ.

## NFS storage

The server is `192.168.0.189`. Each acquisition machine mounts a different
server directory at the same local path, `/mnt/testbench-results`. That local
path is only the *mount point*: after mounting, writing there writes directly
to the server, not to the acquisition machine's disk.

### Configure the server

Run these commands on `192.168.0.189`:

```bash
sudo apt update
sudo apt install nfs-kernel-server
sudo mkdir -p /srv/testbenchdaq/wentelteef /srv/testbenchdaq/knarskast
```

On each acquisition machine, find the IP address and the UID/GID of the user
that runs TestbenchDAQ. For example, on `wentelteef`:

```bash
hostname
hostname -I
id -u
id -g
```

Use those real values below. If the TestbenchDAQ user is UID/GID `1000`, give
that user ownership of the server folders:

```bash
sudo chown 1000:1000 /srv/testbenchdaq/wentelteef
sudo chown 1000:1000 /srv/testbenchdaq/knarskast
sudo chmod 2775 /srv/testbenchdaq/wentelteef /srv/testbenchdaq/knarskast
```

Edit `/etc/exports` on the server with `sudoedit /etc/exports` and add one line
per acquisition machine. Replace the example IP addresses with the values from
`hostname -I` above:

```text
/srv/testbenchdaq/wentelteef 192.168.0.50(rw,sync,no_subtree_check)
/srv/testbenchdaq/knarskast 192.168.0.51(rw,sync,no_subtree_check)
```

Apply and inspect the exports:

```bash
sudo exportfs -ra
sudo exportfs -v
sudo systemctl enable --now nfs-server
```

The `exportfs -v` output must show both paths and the correct client IP
addresses. If UFW is enabled, allow NFS from only the acquisition machines.

### Mount storage on `wentelteef`

Run these commands on `wentelteef`, not on the server:

```bash
sudo apt update
sudo apt install nfs-common
sudo mkdir -p /mnt/testbench-results
sudo mount -t nfs4 \
  192.168.0.189:/srv/testbenchdaq/wentelteef \
  /mnt/testbench-results
```

Verify that it is truly mounted and writable as the normal operator user:

```bash
mountpoint /mnt/testbench-results
findmnt -T /mnt/testbench-results
touch /mnt/testbench-results/test-write
rm /mnt/testbench-results/test-write
```

Make it persistent across reboot by adding exactly this line to
`/etc/fstab` on `wentelteef`:

```text
192.168.0.189:/srv/testbenchdaq/wentelteef /mnt/testbench-results nfs4 rw,hard,_netdev,x-systemd.requires=network-online.target 0 0
```

Then test it without rebooting:

```bash
sudo mount -a
mountpoint /mnt/testbench-results
```

### Mount storage on `knarskast`

Repeat the same commands on `knarskast`, changing only the exported folder:

```bash
sudo apt update
sudo apt install nfs-common
sudo mkdir -p /mnt/testbench-results
sudo mount -t nfs4 \
  192.168.0.189:/srv/testbenchdaq/knarskast \
  /mnt/testbench-results
```

Its `/etc/fstab` line is:

```text
192.168.0.189:/srv/testbenchdaq/knarskast /mnt/testbench-results nfs4 rw,hard,_netdev,x-systemd.requires=network-online.target 0 0
```

### Configure TestbenchDAQ

Each machine uses a matching configuration:

```json
{
  "machine_name": "wentelteef",
  "output_root": "/mnt/testbench-results"
}
```

For a complete `wentelteef` Gator command-line example using this mount, see
[the user manual's worked run example](user-manual.md#worked-example-a-30-run-gator-test-on-wentelteef).

Before hardware is commanded, TestbenchDAQ verifies that `output_root` is
writable NFS/NFSv4 storage. For a deliberately local run, an operator may use
`--allow-local-output`; the manifest prominently records that override. The
option is never persisted in `config.json`.

### Troubleshooting: `access denied by server while mounting`

Successful `ping` only proves the machines can reach each other. This mount
error means the server did not authorize the acquisition machine's IP address
for that exported path. On the **server**, run:

```bash
sudo exportfs -v
sudo cat /etc/exports
sudo systemctl status nfs-server --no-pager
```

On the **acquisition machine**, obtain the exact IPv4 address that the server
sees:

```bash
ip -4 addr show
```

Ensure that exact address appears beside the correct path in `/etc/exports`.
After any `/etc/exports` change, run `sudo exportfs -ra` on the server, then
retry the mount. For diagnosis only, the server can list its exports with:

```bash
showmount -e 192.168.0.189
```

Do not use a broad `*(rw,...)` export as a permanent fix; authorize each test
machine explicitly.

### Troubleshooting: mount works, but `touch` reports `Permission denied`

This means NFS mounting is correct, but the server directory permissions do
not match the numeric user/group IDs on the acquisition machine. NFS uses
numbers such as `1000:1000`, not the displayed username.

On the **acquisition machine**, run:

```bash
id
```

Note the `uid` and `gid`. On the **server**, inspect the directory using
numeric IDs:

```bash
sudo ls -ldn /srv/testbenchdaq/wentelteef
```

If `id` on `wentelteef` reports UID `1000` and GID `1000`, fix that server
directory with:

```bash
sudo chown 1000:1000 /srv/testbenchdaq/wentelteef
sudo chmod 2775 /srv/testbenchdaq/wentelteef
```

Use the actual values from `id`, then retry on the acquisition machine:

```bash
touch /mnt/testbench-results/test-write
rm /mnt/testbench-results/test-write
```

No unmount or server restart is needed after changing ownership or mode.

### Network speed and long-running tests

Gator data is written continuously to `output_root`. If NFS cannot sustain the
CSV write rate, the native recorder's two 4,096-sample buffers eventually fill
and its acquisition callback waits for storage. The application does not drop
files silently, but a blocked callback can lead to delayed or missing samples
from the vendor device. A slow or disconnected `hard` NFS mount can also make a
running process wait in filesystem I/O rather than fail immediately.

enDAQ behaves differently: it records to its own device during the measurement
window, then copies and SHA-256 verifies the IDE file to NFS after stopping. A
slow connection therefore extends the cleanup/offload period. In prognostic
mode it can make the next planned run late; the default `abort` policy stops
the session rather than silently drifting the schedule.

Use wired Gigabit Ethernet, not Wi-Fi, for acquisition storage. Test both the
network link and the actual NFS mount before a long test. On the server, start
an `iperf3` listener:

```bash
sudo apt install iperf3
iperf3 -s
```

On the acquisition machine, test the link:

```bash
sudo apt install iperf3
iperf3 -c 192.168.0.189
```

Then test real NFS write throughput. This creates a 1 GiB temporary file on
the server export and removes it afterward:

```bash
dd if=/dev/zero of=/mnt/testbench-results/nfs-throughput-test.bin \
  bs=16M count=64 conv=fdatasync status=progress
rm /mnt/testbench-results/nfs-throughput-test.bin
```

Finally, perform a short Gator trial with the intended sample rate and inspect
the raw CSV row count. At 5 kHz for 120 seconds, it should be close to 600,000
data rows (plus one header row):

```bash
wc -l /mnt/testbench-results/SESSION_ID/run_01/raw/gator/gator_channel.csv
```

The sustained measured NFS write rate should comfortably exceed the Gator CSV
rate; leave at least a two-times margin. If it does not, lower the Gator sample
rate, improve the wired network/server disk, or change the acquisition design
to use a suitable local high-speed buffer.

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
