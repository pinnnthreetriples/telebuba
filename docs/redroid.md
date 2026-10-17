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
  only: Android has no ports of its own, its adbd listens in the gateway's namespace.
- **Start**: create what is missing (gateway first), stop whatever of the pod runs, copy the
  account's current proxy into the gateway, start it, wait until `net-gateway ready` (tun0 up,
  tunnel running, routing in place; `EMULATOR__TUNNEL_TIMEOUT_SECONDS`), then create/start Android.
  A failed start removes what it created and stops what it found. **Stop** is Android, then the
  gateway; **remove** deletes both (a host without docker has nothing to remove).
- **Identity**: a block appended to the image's `/product/etc/build.prop` (read last by init, so it
  wins over `/vendor`'s own) before every cold boot (not on the
  command line: redroid's init re-splits it on spaces, so `Pixel 7` would become `Pixel`); the
  image is the one `EMULATOR__REDROID_IMAGES` names for the fingerprint's SDK. See
  [Identity and region](#identity-and-region).
- **Window**: `scrcpy --serial … --window-title <container>`, one per container.

Android keeps its `/data` across stop/start (it is re-created only when its gateway is gone, since
its namespace went with it); deleting the account runs `docker rm --force` on both.

## The gateway's network

- **Tunnel**: [hev-socks5-tunnel](https://github.com/heiher/hev-socks5-tunnel) (version and
  sha256 pinned in the Dockerfile) on `tun0`, MTU 1400, with policy routing of its own: every
  unmarked packet goes to a table whose default route is tun0; the tunnel's own sockets carry a
  fwmark that routes them out of eth0. Android's netd moves eth0's default route out of `main`
  and flushes routing rules when it starts, so the gateway keeps its routes in tables of its own
  and puts its rules back every second.
- **Kill-switch** (nf_tables via `iptables-nft`, which Android's legacy `iptables` cannot touch):
  all egress is dropped before anything else runs; only loopback, tun0, replies to connections
  from outside (adb) and the proxy's own address (its port, and UDP for the relay) are let out.
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
`/dev/net/tun`, the IPv6 sysctls and the adb port binding, nothing of the proxy. The file, and
the tunnel config rendered from it under `/run/net-gateway`, stay readable inside the container
to whoever may `docker exec` (root on the Docker host anyway).

### Changing an account's proxy

Assigning, unassigning or removing the proxy stops the account's pod (after a start in flight,
which may have read the old proxy). Opening a running pod first compares its gateway's proxy file
with the account's current proxy by sha256 (`docker exec … sha256sum`, so the credentials never
leave the container); a pod on another proxy, or whose gateway died, is stopped and started again.

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
  --enable IP_NF_IPTABLES --enable IP_NF_FILTER --enable IP6_NF_IPTABLES --enable IP6_NF_FILTER
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

Then `winget install Genymobile.scrcpy`, pull the images named in `EMULATOR__REDROID_IMAGES`, and
build the gateway image under the tag `EMULATOR__GATEWAY_IMAGE` names:

```bash
docker build -t telebuba/net-gateway:1 docker/net-gateway
```

## Settings

```env
EMULATOR__ENABLED=true
# The adb next to scrcpy.exe: scrcpy starts its own adb, and two versions kill each other's server.
EMULATOR__ADB_PATH=C:/Users/<you>/AppData/Local/Microsoft/WinGet/Packages/Genymobile.scrcpy_Microsoft.Winget.Source_8wekyb3d8bbwe/scrcpy-win64-v4.1/adb.exe
EMULATOR__TELEGRAM_APK_PATH=emulator/telegram.apk  # an x86_64-capable APK: redroid has no ARM translation
```

`EMULATOR__CPU_CORES` and `EMULATOR__MEMORY_MB` size new containers; the screen is the phone's.
