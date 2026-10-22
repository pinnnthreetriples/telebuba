# redroid: the mobile pod under Docker

A mobile account is opened in a **pod** of two containers (`core/emulator/pod.py`; the flow is
`services/accounts/mobile_emulator.py`):

- `<name>-net`, the **net-gateway** (`docker/net-gateway`, `core/emulator/_gateway.py`): it owns
  the network namespace and sends everything through the account's SOCKS5 proxy, or nothing;
- `<name>`, the [redroid](https://github.com/remote-android/redroid-doc) **Android** container
  (`core/emulator/redroid.py`), created with `--network container:<name>-net`.

Android itself knows nothing of the proxy: no VPN, no in-app setting. It presents the mobile
fingerprint's maker, model and SDK and is signed in through `adb`. Docker has no window, so the
phone is shown in a scrcpy window opened by the phone button.

## How a pod is run

- **Found again** by name (`EMULATOR__INSTANCE_PREFIX` + account id) among containers labelled
  `telebuba.emulator` (Android) and `telebuba.gateway` (gateways); both carry the pod's
  **index** in `telebuba.index`, the lowest index free in either list at create.
- **adb** is published by the GATEWAY on `127.0.0.1:(EMULATOR__REDROID_FIRST_ADB_PORT + index)`
  only: Android has no ports of its own, its adbd listens in the gateway's namespace. redroid's
  adbd takes no key (`ro.debuggable=1`, no `ro.adb.secure`), so no other container may reach it:
  gateways join their own bridge network (`EMULATOR__GATEWAY_NETWORK`, created when missing with
  `com.docker.network.bridge.enable_icc=false`), and the gateway drops 5555 on eth0 from anything
  but the Docker gateway, which is where the published loopback port arrives from.
- **Start**: pull or build a missing image, create what is missing (gateway first), stop whatever of the pod runs, copy the
  account's current proxy into the gateway, start it, wait until `net-gateway ready` (tun0 up,
  tunnel running, routing in place; `EMULATOR__TUNNEL_TIMEOUT_SECONDS`), then create/start Android.
  A failed start removes what it created and stops what it found; an open that fails once a new
  container booted (its SDK checked) only stops it, so its seeded `/data` spares the next try a
  first boot and a seeding. **Stop** is Android, then the
  gateway; **remove** deletes both (a host without docker has nothing to remove).
- **Identity**: a block appended to the image's `/product/etc/build.prop` (read last by init, so it
  wins over `/vendor`'s own) before every cold boot (not on the
  command line: redroid's init re-splits it on spaces, so `Pixel 7` would become `Pixel`); the
  image is the one `EMULATOR__REDROID_IMAGES` names for the fingerprint's SDK (by default the
  64-bit-only `*_64only-latest` tags, published for amd64 and arm64 alike). See
  [Identity and region](#identity-and-region).
- **Boot arguments**: the screen, the serial, `androidboot.use_memfd=true` (memfd instead of the
  deprecated ashmem) and `androidboot.redroid_gpu_mode`; with shared /data also
  `androidboot.use_redroid_overlayfs=1`. See [GPU and shared /data](#gpu-and-shared-data).
- **Window** (only with `EMULATOR__DESKTOP_VIEWER=true`): `scrcpy --serial … --window-title <container>`,
  one per container. Off by default: the window encodes video for as long as it is open, warming
  visits included. The web screen below replaces it.

Android keeps its `/data` across stop/start (it is re-created only when its gateway is gone, since
its namespace went with it); deleting the account runs `docker rm --force` on both. With shared
/data on, `/data` lives in the pod's diff directory instead, which outlives the container: an
Android made again finds the account's sign-in there, and only deleting the account removes it.

**Coming from #551** (one container per account, no gateway): such a container has no gateway, so
the first start after the upgrade removes it and makes the pod anew. Its `/data` goes with it,
the Telegram app's sign-in included: the next open signs the app in again with a login code.

## The gateway's network

- **Tunnel**: [hev-socks5-tunnel](https://github.com/heiher/hev-socks5-tunnel) (version and
  sha256 pinned in the Dockerfile) on `tun0`, MTU 1400, with policy routing of its own: every
  unmarked packet goes to a table whose default route is tun0; the tunnel's own sockets carry a
  fwmark that routes them out of eth0. Android's netd moves eth0's default route out of `main`
  and flushes routing rules when it starts, so the gateway keeps its routes in tables of its own
  and puts its rules back every second.
- **Kill-switch** (nf_tables via `iptables-nft`, which Android's legacy `iptables` cannot touch):
  all egress is dropped before anything else runs; only loopback, tun0, replies to connections
  from outside (adb; matched as conntrack's REPLY direction, so a flow Android opened through
  tun0 cannot leave by eth0 while netd has flushed the routing rules) and the proxy's own
  address (its port, and UDP for the relay) are let out. Any rule that fails stops the gateway
  before its tunnel starts.
  A packet sent before the tunnel is up, while the rules are being put back, or after the tunnel
  dies is dropped, never sent direct; a dead tunnel stops the gateway.
- **DNS**: every UDP query to port 53 is sent to the tunnel's mapped DNS, which answers with a
  placeholder address and lets the proxy resolve the name on connect. Android's opportunistic
  private DNS (DoT to its resolver) also goes through the tunnel. Nothing resolves on eth0.
- **IPv6** is off (sysctls at create) and dropped by `ip6tables` too: privileged Android turns
  link-local IPv6 back on.
- **MSS** of outgoing SYNs is clamped to the path MTU (tun0's 1400).
- **Proxy host**: resolved to IPv4 on the host by the app, so a hostname works and the gateway
  needs no DNS before its tunnel is up. A host with no IPv4 address is refused.

### Where the proxy's credentials go

They are written to a scratch directory (owner-only) on the host, `docker cp`'d into the stopped
gateway as `/etc/net-gateway/proxy`, and the scratch copy is deleted right after. They are never
in an env var (`docker inspect` shows env, `docker create -e` puts them in argv), an argv, a log
line or an error. `docker inspect` of a gateway shows its image, labels, NET_ADMIN,
`/dev/net/tun`, the IPv6 sysctls and the adb port binding, nothing of the proxy. The file stays
in the container's layer and the tunnel config rendered from it in a tmpfs on `/run`; both are
readable to whoever may `docker exec` (root on the Docker host anyway).

### Changing an account's proxy

Assigning, unassigning or removing the proxy stops the account's pod (after a start in flight,
which may have read the old proxy). Opening a running pod first compares its gateway's proxy file
with the account's current proxy by sha256 (`docker exec … sha256sum`, so the credentials never
leave the container); a pod on another proxy, whose gateway died, or whose gateway was started
again after Android (`State.StartedAt`: Android is then left in the old namespace, cut off) is
stopped and started again.

## Identity and region

The container is a real phone (`core/emulator/_profile.py`):

- **Firmware**: `configs/oem_blueprints/*.json` holds one file per phone of the catalogue
  (`core/device_fingerprint_android.py`), with one real build per Android SDK it ran, taken from
  public firmware listings. `ro.product.*` (every partition), `ro.build.*` and
  `ro.build.fingerprint` / `ro.build.description` (put together from the build's fields) name
  that phone and build. `ro.sf.lcd_density` and the screen (`androidboot.redroid_width/height/dpi`)
  are the phone's container screen. Left to the image: `ro.build.version.sdk` (the SDK check
  reads it), `ro.hardware` / `ro.board.platform` (they pick the HALs it boots with) and the
  security flags adbd depends on.
- **Serial and IMEI** are derived from the account id, so the account keeps its phone when its
  container is made again. The serial goes on the command line (`androidboot.serialno`): init
  sets `ro.serialno` from it before any `build.prop` is read. redroid has no telephony, so the
  IMEI (the model's TAC + Luhn) is not given to the container.
- **Region**: the timezone (`persist.sys.timezone`, `core/country_zones.py`, the web window's
  table too) and the SIM operator (`gsm.sim.operator.*`, `gsm.operator.*`, from
  `configs/carriers_mcc_mnc.json`, one carrier per account) follow the proxy's country, or the
  phone number's while the proxy has none on record. A country without carriers gets no SIM
  properties; one without a zone gets `Etc/UTC`. The language (`persist.sys.locale`) is the
  mobile fingerprint's `system_lang_code`, which the app sends Telegram as that same field.
- A stopped container gets its `build.prop` rewritten before it boots, so a proxy that moved
  country moves the timezone and carrier; a running one is left alone.
- **CPU** (`core/emulator/_topology.py`): a phone whose blueprint names its chip (`soc`, one
  `configs/soc/*.json` each, read from public cpuinfo/cpufreq dumps; `mt6877` has no public
  cpuinfo, so its A55/A78 ids and `Features` are the ones every other such dump shows and its
  BogoMIPS follows its 13 MHz timer; A78 is MediaTek's spec for its big cores, where ruby's
  kernel DT carries a generic `cortex-a76` compatible) gets `ro.soc.model` /
  `ro.soc.manufacturer` and as many host cores as the chip has (fewer on a smaller Docker VM),
  pinned with `--cpuset-cpus`: pod N gets its own slice while the VM has room for it, else the
  first one. A new container sees the chip: `/sys/devices/system/cpu` is replaced read-only by a
  generated tree (cpufreq, topology; WSL2's own has no cpufreq), and `/proc/cpuinfo` is bound by
  an init script (`/vendor/etc/init/cpuinfo.rc`) from `/dev/cpuinfo` at every boot: init mounts
  a fresh proc over Docker's, so Docker cannot mount it there. The files live once per chip and
  core count under `EMULATOR__CPU_TOPOLOGY_DIR`. A container made before gets only the cpuset
  (`docker update`). A phone without a chip keeps `EMULATOR__CPU_CORES` of the host's CPUs.
  The image stays x86_64 (`ro.product.cpu.abilist` starts with `x86_64`), so an app comparing
  its ABI with the ARM cpuinfo can tell.

## First-boot data

A fresh container has no contacts and no photos. After its first boot (and the SDK check),
while the app is installed and signed in (nothing there reads the address book; the open still
waits for the seeding), `core/emulator/_seeder.py` gives it both (what they are comes from
`_seed_data.py`), drawn from the container's serial so a container made again for the account
gets the same ones:

- **Contacts**: 25–40 people with names of the proxy country's language
  (`configs/contact_names.json`, `en_US` for a country it lacks) and that country's valid mobile
  numbers (`phonenumbers`), 70% written internationally; about a fifth have an e-mail and a
  company. They go in through the Contacts Provider (`content insert`), tagged `sync1=telebuba`
  and `sync2=<place in the list>`, so each contact's rows go under its own raw contact.
- **Photos**: 12–20 JPEGs in `/sdcard/DCIM/Camera`, named the way the phone's camera app names
  them (the blueprint's `camera.file_style`), with that phone's EXIF: `Make`/`Model`, the build's
  incremental as `Software` on a Samsung (other makers' camera apps write their own version or
  none, so it is left out), the main lens's `FNumber`/`FocalLength` (the blueprint's `camera`),
  ISO, exposure and a capture time 5–45 days back at a daytime hour in the container's timezone.
  Each file's mtime is set to that time (`touch -t`, stamped in UTC under `TZ=UTC0`), then
  MediaStore is asked to index the volume (`content call … scan_volume`, the per-file scan
  broadcast when the provider refuses it). EXIF carries `ExifVersion` and an offset for each time.

Both run on the device as `sh` scripts, at the same time. Each `content` call starts an
app_process (about a second, mostly waiting; ~135 for an address book), so the contacts are dealt
over 8 background lanes written at once (40 contacts in 25 s on a redroid 13 pod, against ~160 s
one after another). `content` exits 0 even when the provider refused a row, so every call goes through
a wrapper that fails on the error it prints; a lane stops at its first failure, the script waits for
every lane, fails when any did, and ends by counting the tagged contacts.
Only when both are in is `/data/local/tmp/.telebuba_seeded` written; a container that has it is
never seeded again. A seeding that fails, for any reason but a cancellation, is logged
(`account_mobile_seed_failed`) and the sign-in goes on; the next boot redoes it, deleting the
tagged contacts and the photos the earlier try listed in `/data/local/tmp/.telebuba_photos`
(written before its push) first.
Android keeps `/data` across stop/start, so only a container made again is seeded again.

## A phone stint prepares its phone

Starting warming with device `mobile` (`services/warming/_mobile_prep.py`) first checks the host
(`core/emulator/host.py`) and refuses the start, stint untouched, with what only the operator can
fix: `emulator_docker_unavailable` (no docker CLI or its daemon down), `emulator_adb_missing`, or
`emulator_binder_missing` (the kernel below; probed in a throwaway container of `alpine:3.20`, the
gateway's base image, pulled first when missing, and not asked again once found capable; a pull
that fails is `emulator_image_unavailable`), or, with `EMULATOR__GPU_MODE=host`,
`emulator_gpu_missing` (no `/dev/dri`; probed the same way). Then a background job readies the rest while the card
says "preparing the phone": the scrcpy-server, and the pod itself with its images pulled or built, Telegram installed and signed in
through the account's existing session, parked for the first visit within
`EMULATOR__MAX_RUNNING_PODS` (a host still full after 30 minutes fails it as `emulator_capacity`); the job ends with the stint's loop, so Stop or Promote cancels it. Visits are skipped as `emulator_preparing` while it runs; a failed
job leaves its code on the card until a visit warms. A visit that finds the app signed out signs it
in, so a phone the job could not finish (or one prepared before a restart) heals on its own.

## GPU and shared /data

Both are fixed when an Android container is created: a container made before a change keeps how
it was made until it is made again (its gateway gone, or the account deleted and added back).

**GPU** (`EMULATOR__GPU_MODE`, `core/emulator/host.py`): every container is created with
`androidboot.redroid_gpu_mode=guest|host`.

- `guest` (default): software rendering, any host, no probe.
- `host`: the host's GPU; the container is given `--device /dev/dri:/dev/dri`. A host without
  `/dev/dri` (probed in a throwaway `alpine:3.20` given that device) is refused as
  `emulator_gpu_missing`, at the phone stint's host check and at a pod's create.
- `auto`: `host` when the probe finds `/dev/dri`, else `guest`. A GPU found is not probed again
  while the process runs; a missing one is, so a driver installed later is picked up.

Servers without a GPU (Ampere Altra, Graviton) stay on `guest`. On an RK3588 board `/dev/dri`
appears with the Mali driver (panthor, or the vendor kernel's own) and Mesa on the host.

**Shared /data** (`EMULATOR__DATA_BASE_DIR` + `EMULATOR__DATA_DIFF_DIR`, `core/emulator/_data.py`):
off unless both are set to absolute directories on the Docker host (one without the other, or a
relative one, fails the settings at startup; empty is off). Then a new Android is created with

```text
--volume <DATA_BASE_DIR>/<sdk>:/data-base:ro
--volume <DATA_DIFF_DIR>/<container>:/data-diff
androidboot.use_redroid_overlayfs=1
```

so its `/data` is the overlay of the SDK's shared base, read-only, under its own diff, where
everything the phone writes lands. A start that is about to make Android checks the base first
and refuses, before anything is created, as `emulator_data_base_missing` when
`<DATA_BASE_DIR>/<sdk>` is not a directory; the diff is made when missing, and a start that fails
removes the diff it made (never one it found). A start that finds its Android still there needs
no base. An Android made again (its gateway gone) stays on the diff its predecessor recorded,
wherever the setting points now; with shared /data turned off since, it keeps that label without
mounting it. The diff directory may not be a filesystem root or contain `..`. The container records its diff in the `telebuba.data_diff`
label, so deleting the account removes the recorded diff and the one configured now, even after
`EMULATOR__DATA_DIFF_DIR` moved or was unset. Android is stopped first; each diff goes before the
containers (so one that cannot go stays named by the label for a later try), in a throwaway
`alpine:3.20` running as root (Android wrote it as its own users). Pods made before it was turned
on are not moved: they keep `/data` in the container until they are made again.

### Preparing /data-base

One base per SDK in `EMULATOR__REDROID_IMAGES`, made by the operator from a phone that has booted
once and done nothing else: never started by the app, so it holds no Telegram, contacts, photos or
seeding marker. On the Docker host, as root (`34` and its image; repeat for `33`):

```bash
SDK=34 IMAGE=redroid/redroid:14.0.0_64only-latest ROOT=/srv/telebuba
mkdir -p "$ROOT/template-$SDK" "$ROOT/data-base/$SDK" "$ROOT/data-diff"
docker run -d --name telebuba-template --privileged -v "$ROOT/template-$SDK:/data" \
  "$IMAGE" androidboot.use_memfd=true androidboot.redroid_gpu_mode=guest
until [ "$(docker exec telebuba-template getprop sys.boot_completed)" = 1 ]; do sleep 2; done
sleep 30 && docker stop telebuba-template && docker rm telebuba-template
cd "$ROOT/template-$SDK"
rm -f system/users/0/settings_ssaid.xml system/users/0/settings_secure.xml \
  system_ce/0/accounts_ce.db* system_de/0/accounts_de.db* misc/adb/adb_keys \
  property/persistent_properties
rm -rf misc/keystore/* local/tmp/.telebuba_*
cp -a . "$ROOT/data-base/$SDK/" && cd / && rm -rf "$ROOT/template-$SDK"
```

What is removed is what tells one phone from another: the per-app Android IDs (SSAID), the secure
settings that carry the device's own ids, the keystore's keys, any accounts, adb's keys and the
persisted properties; each pod writes its own into its diff on its first boot. `cp -a` keeps the
owners, modes and extended attributes Android needs. Then set
`EMULATOR__DATA_BASE_DIR=/srv/telebuba/data-base` and `EMULATOR__DATA_DIFF_DIR=/srv/telebuba/data-diff`.
Never write into a base pods are running on: overlayfs does not expect its lower layer to change
under it. To change a base, stop the pods, replace it, and make their Androids again.

## Host setup (Windows, Docker Desktop on WSL2)

redroid needs binder in the kernel, and Android's netd needs the legacy iptables filter tables
its own `iptables` uses. The stock WSL2 kernel has `CONFIG_ANDROID_BINDER_IPC` unset and the
filter tables only as modules nobody loads, so build Microsoft's kernel of the same version with
all of them built in. (The gateway's nf_tables rules, `xt_conntrack`, `TCPMSS`, NAT and TUN work
with that kernel as it is; it has no `xt_mark`, so the kill-switch matches addresses, not marks.)
Inside the
Ubuntu distro (as root for the packages, as the user for the build):

```bash
apt-get install -y build-essential flex bison dwarves libssl-dev libelf-dev bc python3 cpio kmod qemu-utils git
git clone --depth 1 --branch linux-msft-wsl-6.6.87.2 https://github.com/microsoft/WSL2-Linux-Kernel.git ~/WSL2-Linux-Kernel
cd ~/WSL2-Linux-Kernel && cp Microsoft/config-wsl .config
scripts/config --enable ANDROID_BINDER_IPC --enable ANDROID_BINDERFS \
  --set-str ANDROID_BINDER_DEVICES "binder,hwbinder,vndbinder" --disable LOCALVERSION_AUTO \
  --enable IP_NF_IPTABLES --enable IP_NF_FILTER --enable IP6_NF_IPTABLES --enable IP6_NF_FILTER \
  --enable NETFILTER_XT_TARGET_HL --enable IP_NF_TARGET_TTL
make olddefconfig && make -j"$(nproc)"
make INSTALL_MOD_PATH="$PWD/modules" modules_install
sudo ./Microsoft/scripts/gen_modules_vhdx.sh "$PWD/modules" "$(make -s kernelrelease)" modules.vhdx
mkdir -p /mnt/c/wsl-kernel && cp arch/x86/boot/bzImage modules.vhdx /mnt/c/wsl-kernel/
```

Point WSL at it in `%UserProfile%\.wslconfig`, then `wsl --shutdown` and start Docker Desktop:

```ini
[wsl2]
kernel=C:\\wsl-kernel\\bzImage
kernelModules=C:\\wsl-kernel\\modules.vhdx
```

Check: `wsl -d Ubuntu -- grep binder /proc/filesystems` prints a `binder` line. To undo, remove
the two lines and `wsl --shutdown`. WSL updates no longer bring kernel fixes: rebuild with the
new tag when Microsoft ships one.

Then `winget install Genymobile.scrcpy` (for its `adb`). The images need nothing by hand: before a
pod's first start (`core/emulator/images.py`) a missing redroid image for the phone's SDK is pulled,
and a missing `EMULATOR__GATEWAY_IMAGE` is built from `docker/net-gateway`, each bounded by
`EMULATOR__IMAGE_TIMEOUT_SECONDS` (`emulator_image_unavailable` when it fails). An image already
present is never pulled or rebuilt; after changing `docker/net-gateway`, bump the tag.

## Host setup (Linux ARM64)

The production hosts: Ubuntu or Debian on arm64 (Ampere Altra, AWS Graviton, RK3588 boards), the
app and Docker on the same machine, ext4 under the shared /data directories. The images are the
`*_64only` tags: Ampere and other cores without 32-bit EL0 cannot run a 32-bit userspace, and
Telegram's universal APK carries `arm64-v8a`.

```bash
apt-get install -y docker.io adb
# Stock Ubuntu kernels ship binder_linux here; a vendor kernel has no such package (see below).
apt-get install -y "linux-modules-extra-$(uname -r)" || true
echo 'options binder_linux devices=binder,hwbinder,vndbinder' > /etc/modprobe.d/binder.conf
printf '%s\n' binder_linux ip_tables iptable_filter ip6_tables ip6table_filter \
  > /etc/modules-load.d/telebuba.conf
systemctl restart systemd-modules-load
```

Check: `grep binder /proc/filesystems` prints a `binder` line (the app probes the same at a
phone stint's start, `emulator_binder_missing` otherwise). The iptables modules are the legacy
filter tables Android's netd uses, as on WSL. A vendor kernel without `binder_linux` (some RK3588
images) needs `CONFIG_ANDROID_BINDER_IPC`, `CONFIG_ANDROID_BINDERFS` and
`CONFIG_ANDROID_BINDER_DEVICES="binder,hwbinder,vndbinder"` built in. memfd needs nothing:
`androidboot.use_memfd=true` replaces ashmem, which these kernels no longer have.

Set `EMULATOR__ADB_PATH=adb`; scrcpy itself is needed only with `EMULATOR__DESKTOP_VIEWER=true`
(the web screen pushes its own scrcpy-server). Then pick a GPU mode and, if wanted, prepare the
shared /data bases ([GPU and shared /data](#gpu-and-shared-data)).

## The Telegram app

A container with no Telegram gets `EMULATOR__TELEGRAM_APK_PATH`. A missing file is downloaded
(`core/emulator/assets.py`, at startup and again wherever it is needed; for a new container,
while its pod starts and boots, so a file that cannot be had fails the open then) from telegram.org's own
link, which always serves the latest universal build (arm64-v8a and x86_64 included; package
`org.telegram.messenger.web`) and publishes no checksum: the file is kept only when every signer
of its v2/v3/v3.1 blocks carries Telegram's certificate (SHA-256 `49c15225…dcc38e1`, pinned in code), and Android checks
the signature against that certificate on install. A file already there (the operator's own APK
included) is never replaced or updated. The package and `versionName (versionCode)` are read off
the APK (`core/android_apk.py`): new mobile fingerprints claim that version unless
`EMULATOR__TELEGRAM_APP_VERSION` is set, and a container is driven through the Telegram it already
has (`EMULATOR__TELEGRAM_PACKAGE` first, else the APK's), so a phone set up from an older
`org.telegram.messenger` APK keeps its app and its sign-in.

Each screen read is one `uiautomator dump` (`core/emulator/adb.py`). On Docker Desktop/WSL2 it
costs about 2 s: about 0.1 s VM start, then uiautomator's fixed 1 s wait for an idle screen, a few
milliseconds of fetching, and about 0.8 s of VM shutdown after the file is written. The dump script
kills uiautomator at its "dumped to" print, so a read takes about 1.25 s. The idle wait is
hardcoded in uiautomator, and only a long-lived dump server of our own on the phone could
shorten it.

## Web screen and the device farm

The **Эмуляторы** page (`/devices`, `api/v1/devices.py`) lists every mobile account's phone with its
status (asleep, starting, free, warming, manual), starts and puts it to sleep, and opens its live
screen; the warming card shows an eye while a mobile visit runs. The screen is a WebSocket
(`/api/v1/devices/{id}/screen?mode=view|control`, `core/emulator/scrcpy.py`):

- **On demand only.** Opening the socket pushes `scrcpy-server` to the phone, forwards a host port
  (`adb forward tcp:0`) and starts the server through `adb shell`; closing it (tab closed, network
  dropped, cancelled) closes the sockets, waits for the server, kills it by its `scid` if it lingers,
  and removes the forward. Nothing encodes while nobody watches, and a sleeping phone costs nothing.
- **One viewer per phone**: a new one supersedes the old (`screen_superseded`), so two encoders never
  run on one phone. `view` starts the server without a control socket; `control` holds the phone
  (`manual`) and excludes a warming visit, and the other way round.
- **The video** is passed through as the server writes it (codec header, then a 12-byte header per
  frame: `raw_stream` stays off, the decoder needs the keyframe flags); the browser decodes h264 in
  hardware (`@yume-chan/scrcpy` + `scrcpy-decoder-webcodecs`, loaded only when a screen opens).
- **Commands** arrive as JSON (`schemas/device_farm.py`: touch, scroll, key, text, clipboard) and the
  backend writes scrcpy's bytes (`core/emulator/_scrcpy_control.py`): anything else (start an app,
  UHID) has no way in. Every handshake needs the session cookie and the page's own Origin.

The server's version must match the decoder's protocol exactly; `@yume-chan/scrcpy` 2.3 speaks up
to **3.3.1** (the desktop scrcpy may be newer). A missing file is downloaded on the first screen
(`core/emulator/assets.py`) from that version's GitHub release to `EMULATOR__SCRCPY_SERVER_PATH`,
and kept only when its sha256 is `EMULATOR__SCRCPY_SERVER_SHA256`; a file already there is used as
it is, never updated. Another version needs its own hash in that setting. A phone is one account's
pod today; the API already lists a phone's apps (`platform`), so other platforms can join it later.

## Settings

```env
EMULATOR__ENABLED=true
# The adb next to scrcpy.exe: scrcpy starts its own adb, and two versions kill each other's server.
EMULATOR__ADB_PATH=C:/Users/<you>/AppData/Local/Microsoft/WinGet/Packages/Genymobile.scrcpy_Microsoft.Winget.Source_8wekyb3d8bbwe/scrcpy-win64-v4.1/adb.exe
EMULATOR__TELEGRAM_APK_PATH=emulator/telegram.apk  # fetched from telegram.org when missing
```

`EMULATOR__CPU_CORES` (phones without a chip) and `EMULATOR__MEMORY_MB` size new containers;
the screen is the phone's.
