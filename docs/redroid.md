# redroid: the mobile pod under Docker

A mobile account is opened in a **pod** of two containers (`core/emulator/pod.py`; the flow is
`services/accounts/mobile_emulator.py`):

- `<name>-net`, the **net-gateway** (`docker/net-gateway`, `core/emulator/_gateway.py`): it owns
  the network namespace and sends everything through the account's SOCKS5 proxy, or nothing;
- `<name>`, the [redroid](https://github.com/remote-android/redroid-doc) **Android** container
  (`core/emulator/redroid.py`), created with `--network container:<name>-net`.

Both are created with `--cgroupns private` (their own cgroup tree, not the host's): Docker's
default on cgroup v2 hosts, set anyway for v1. The flag needs Docker Engine 20.10 (API 1.41) or
newer.

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
  tunnel running, routing in place; `EMULATOR__TUNNEL_TIMEOUT_SECONDS`, probed every 0.2 s; a
  gateway that exited meanwhile fails the start at once), then create/start Android.
  A failed start removes what it created and stops what it found; an open that fails once a new
  container booted (its SDK checked) only stops it, so its seeded `/data` spares the next try a
  first boot and a seeding. A new Android on a shared-/data diff an earlier one left (its
  sign-in) is only stopped too, whenever it fails: that diff is never removed by a failed open.
  **Stop** is Android, then the
  gateway; **remove** deletes both (a host without docker has nothing to remove).
- **Identity**: the phone's properties appended to the image's `/product/etc/build.prop` (read
  last by init, so they win over `/vendor`'s own) before every cold boot, replacing the image's
  lines and our earlier ones for the same keys, with no comment of ours (not on the
  command line: redroid's init re-splits it on spaces, so `Pixel 7` would become `Pixel`); the
  image is the one `EMULATOR__REDROID_IMAGES` names for the fingerprint's SDK on an amd64 Docker
  host (by default the `*.0.0-latest` tags, whose 32-bit OMX service holds the only video encoders),
  `EMULATOR__REDROID_IMAGES_ARM64` on an ARM64 one (`docker info`'s architecture; by default the
  64-bit-only `*_64only-latest` tags, see [Host setup](#host-setup-linux-arm64)). See
  [Identity and region](#identity-and-region).
- **Boot arguments**: the phone's `androidboot.hardware`, the screen, the serial,
  `androidboot.use_memfd=true` (memfd instead of the deprecated ashmem) and
  `androidboot.redroid_gpu_mode`; with shared /data also `androidboot.use_redroid_overlayfs=1`.
  They follow `--entrypoint /init qemu=1`: the image's own entrypoint names `redroid` as the
  hardware, and init keeps a boot argument's first value. See
  [The phone's hardware name](#the-phones-hardware-name) and
  [GPU and shared /data](#gpu-and-shared-data).
- **Window** (only with `EMULATOR__DESKTOP_VIEWER=true`): `scrcpy --serial … --window-title <container>`,
  one per container. Off by default: the window encodes video for as long as it is open, warming
  visits included. The web screen below replaces it.

Android keeps its `/data` across stop/start (it is re-created only when its gateway is gone or
made again, since its namespace goes with it); deleting the account runs `docker rm --force` on
both. With shared /data on, `/data` lives in the pod's diff directory instead, which outlives the container: an
Android made again finds the account's sign-in there, and only deleting the account removes it.

**Coming from #551** (one container per account, no gateway): such a container has no gateway, so
the first start after the upgrade removes it and makes the pod anew. Its `/data` goes with it,
the Telegram app's sign-in included: the next open signs the app in again with a login code.

## Host devices

`--privileged` copies every node of the host's `/dev` into the container's, where any app may
list them: on WSL2 `dxg`, `hvc0`-`hvc7`, `vport0p*`, `sd*`, `vsock`, `mem`, `loop*`, `ram*`,
`tty*`; on an arm64 server whatever it has (for instance `nvme*`, `ttyAMA*`, `kvm`, `tpm0`; RK3588
boards `mali0`, `rga`, `mpp_service`, `dma_heap`). A container that draws in software (`guest`) is
therefore created without it (`core/emulator/redroid.py`, `_UNPRIVILEGED`):

```text
--cap-add ALL --security-opt seccomp=unconfined --security-opt apparmor=unconfined
--device /dev/fuse --device-cgroup-rule 'c *:* rwm' --tmpfs /sys/fs/cgroup
```

- Its `/dev` holds only Android's own nodes (binderfs, `fuse`, `null`, `socket/`, ...): Docker
  gives it none of the host's, and `/sys` stays read-only, so ueventd's coldboot (which writes
  `add` into every `uevent` file) makes none either. A device the host adds while the pod runs
  still reaches ueventd as a uevent and may get a node, as before. Nothing Telegram's `EmuDetector` looks for (`qemu_pipe`, `socket/qemud`, goldfish)
  is added.
- `/sys/fs/cgroup` is a tmpfs: init mounts its cgroup2 there (on Docker's read-only one it
  cannot, and reboots with `bootstrap-apexd-failed`).
- The capabilities and the unconfined seccomp/AppArmor are what privileged gave. Android may
  open any character device it makes (binderfs's major is picked by the host's kernel at boot,
  so it cannot be named at create), no block device. The host needs `/dev/fuse` (FUSE built
  in or loaded), which Android's external storage uses.
- Still visible, as before: sysfs is the host's (`/sys/bus`, `/sys/class` but its
  `power_supply`; on WSL2 `/sys/bus/vmbus`), so are `/proc/tty/drivers`, `/proc/mounts` and
  SELinux `Disabled`; and Android's own `/proc` lets its root write the host's global sysctls.
  With SELinux disabled no file has a context to set: copied files get owners and modes only
  ([Files copied into a container](#files-copied-into-a-container)).

A container on the host's GPU (`host`) stays privileged: what a GPU needs beside `/dev/dri`
(Mali's `dma_heap`, for one) is untried without it. A container made before keeps how it was
made until it is made again.

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
- **IPv6** is off (sysctls at create) and dropped by `ip6tables` too: Android mounts its own
  `/proc` and turns link-local IPv6 back on, privileged or not.
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
  public firmware listings. `ro.product.*` (every partition, `vendor_dlkm` too), `ro.build.*` and
  `ro.build.fingerprint` / `ro.build.description` (put together from the build's fields) name
  that phone and build. `Build.USER` / `Build.HOST` / `Build.TIME` (`ro.build.user`,
  `ro.build.host`, `ro.<partition>.build.date[.utc]`) are that build's, read from the same dump,
  its date in the build server's zone; `Build.BOARD` (`ro.product.board`) is the phone's board as
  its vendor partition names it (`panther`, `kalama`, `s5e8835`). `ro.hardware` and
  `ro.board.platform` are the phone's too ([below](#the-phones-hardware-name)). No value carries
  a word Telegram's `EmuDetector` looks for (goldfish, ranchu, nox, vbox86, generic, sdk,
  emulator...; `tests/core/emulator/test_identity.py`). `ro.sf.lcd_density` and the screen
  (`androidboot.redroid_width/height/dpi`) are the phone's container screen. Left to the image:
  `ro.build.version.sdk` (the SDK check reads it), the security flags adbd depends on, and
  `/system/xbin/su`: adbd runs as `shell` (uid 2000), and the stealth step's root (the boot_id
  bind mount) goes through `su`.
- **Serial and IMEI** are derived from the account id, so the account keeps its phone when its
  container is made again. The serial goes on the command line (`androidboot.serialno`): init
  sets `ro.serialno` from it before any `build.prop` is read. redroid has no radio (no RIL), so
  the IMEI (the model's TAC + Luhn) is not given to the container.
- **Region**: the timezone (`persist.sys.timezone`, `core/country_zones.py`, the web window's
  table too) and the SIM operator (`gsm.sim.operator.*`, `gsm.operator.*`, from
  `configs/carriers_mcc_mnc.json`, one carrier per account) follow the proxy's country, or the
  phone number's while the proxy has none on record. A country without carriers gets no SIM
  properties; one without a zone gets `Etc/UTC`. The language (`persist.sys.locale`) is the
  mobile fingerprint's `system_lang_code`, which the app sends Telegram as that same field.
- A stopped container gets its `build.prop` rewritten before it boots, so a proxy that moved
  country moves the timezone and carrier; a running one is left alone.
- **Battery** (`core/emulator/_battery.py`): the level the account's day has reached on the
  phone's own clock (its timezone, above): unplugged at 88–100% between 06:00 and 09:00, then
  discharging, 3–5% less each hour, never below 15%; plugged in (AC, charging) between 21:30
  and 23:30 and charged 15–25% an hour up to the next morning's level, which it always reaches
  before it is unplugged (all drawn per account and date). So it never jumps, at midnight or
  anywhere else. Set after every boot and at every open of a running pod, so it moves through
  the day across stops and pauses. A phone that refuses it keeps its old level. redroid has no
  battery (`present: false`, level 0) until told `present 1`, which comes last: a present battery
  still at level 0 shuts Android down at once. Its voltage and technology cannot be set
  (`dumpsys battery set` refuses both), so a new container's health HAL reads them from a
  `power_supply` tree instead: `EMULATOR__BATTERY_DIR/<pod index>/battery` (type, present,
  status, health `Good`, technology `Li-ion`, capacity, `voltage_now`, temp, charge counter and
  full charge, in the kernel's units) is mounted read-only over `/sys/class/power_supply` and
  written before every boot from the same day's state, so the voltage matches the level the boot
  starts at (3.65–4.27 V). `dumpsys` then moves level and status through the day; the voltage
  stays what the boot read. Checked on redroid 14: `dumpsys battery` shows present, its level,
  voltage, temperature, `Li-ion` and health good instead of zeros. A container made before has no
  mount until it is made again.
- **CPU** (`core/emulator/_topology.py`): a phone whose blueprint names its chip (`soc`, one
  `configs/soc/*.json` each, read from public cpuinfo/cpufreq dumps; `mt6877` has no public
  cpuinfo, so its A55/A78 ids and `Features` are the ones every other such dump shows and its
  BogoMIPS follows its 13 MHz timer; A78 is MediaTek's spec for its big cores, where ruby's
  kernel DT carries a generic `cortex-a76` compatible) gets `ro.soc.model` /
  `ro.soc.manufacturer` and as many host cores as the chip has (fewer on a smaller Docker VM,
  whose CPU count is read once per process: restart the app after resizing the VM),
  pinned with `--cpuset-cpus`: pod N gets its own slice, and once the VM's slices run out they
  are shared round-robin (pod 8 of a 64-core host with 8-core chips is back on 0-7, pod 9 on
  8-15). A new container sees the chip: `/sys/devices/system/cpu` is replaced read-only by a
  generated tree (cpufreq, topology; WSL2's own has no cpufreq), and `/proc/cpuinfo` is bound by
  an init script (`/vendor/etc/init/cpuinfo.rc`) from `/dev/cpuinfo` at every boot: init mounts
  a fresh proc over Docker's, so Docker cannot mount it there. The files live once per chip and
  core count under `EMULATOR__CPU_TOPOLOGY_DIR`. A container made before gets only the cpuset
  (`docker update`). A phone without a chip keeps `EMULATOR__CPU_CORES` of the host's CPUs as its
  quota and sees at most 8 of them, a phone's count: it is pinned to its pod's slice of 8
  (`--cpuset-cpus`, all of a smaller VM's), so `nproc` says 8 instead of the VM's; a container
  made before is pinned at its next start (`docker update`).
  The image stays x86_64 (`ro.product.cpu.abilist` starts with `x86_64`), so an app comparing
  its ABI with the ARM cpuinfo can tell.
- **Hardware features** (`core/emulator/_features.py`): `pm list features` /
  `hasSystemFeature` answer from the XML files of `/vendor/etc/permissions`, which SystemConfig
  reads at every boot. redroid's declare `android.hardware.ethernet`, a single-touch screen and no
  telephony. Before every cold boot (a container made before included) AOSP's own
  `android.hardware.telephony.gsm.xml`, `touchscreen.multitouch.jazzhand.xml`, `usb.host.xml` and
  `usb.accessory.xml` (`frameworks/native/data/etc`, the same in 13 and 14) are copied in, and
  `android.hardware.ethernet.xml` is emptied. eth0 stays up: the Ethernet service starts for
  `usb.host` too (`EthernetTracker` still brings eth0 up as the default network). Checked on
  redroid 13 and 14: boot, eth0, screencap and Telegram's launch unchanged, no crash. Telegram's
  EmuDetector reads telephony only behind `setCheckTelephony(true)`, which the app never calls, and
  no file names anything it looks for (a test holds that). `faketouch` stays: every touchscreen
  phone declares it.
- **Network** (`core/emulator/_transport.py`, `EMULATOR__NETWORK_TRANSPORT`): AOSP's
  `EthernetTracker` publishes eth0 as `TRANSPORT_ETHERNET`, unmetered, so `ConnectivityManager`
  and the WebView's `navigator.connection` say the phone is wired. Its
  [`config_ethernet_interfaces`](https://android.googlesource.com/platform/packages/modules/Connectivity/+/refs/heads/android13-release/service/ServiceConnectivityResources/res/values/config.xml)
  may name the transport an interface is published with
  ([`createNetworkCapabilities`](https://android.googlesource.com/platform/packages/modules/Connectivity/+/refs/heads/android13-release/service-t/src/com/android/server/ethernet/EthernetTracker.java)),
  and the array is overlayable by vendor and product overlays. Before every cold boot (a container
  made before included) a static overlay of `com.android.connectivity.resources` goes into
  `/product/overlay/ConnectivityResourcesProduct`: `cellular` (default) files eth0 as
  `TRANSPORT_CELLULAR`, metered, as the phone's SIM and LTE properties say; `ethernet` writes
  AOSP's own entry back. The APK is built in Python (binary manifest, a one-entry table byte for
  byte what aapt2 writes, a v1 signature with an ECDSA key derived from a fixed label, so the same
  transport always gives the same bytes); PackageManager trusts any signer on /product but skips an
  unsigned package. Only the label changes: eth0, its address, the gateway's kill-switch and adb
  are untouched, no kernel module is involved. Checked on redroid 13 and 14 (bare containers,
  no gateway): boot and a validated `CELLULAR` default network; on 13 also screencap, and Telegram
  installs, launches and connects as on an unchanged container. What still tells:
  `NetworkInfo.getTypeName()` stays `Ethernet` and `getExtraInfo()` eth0's MAC (the factory's
  legacy name), the specifier is `EthernetNetworkSpecifier`, and `TelephonyManager` has no data
  connection (no RIL). Telegram reads only `getType()`, which is now `TYPE_MOBILE` (its mobile
  auto-download presets apply), and sends neither in `initConnection`. On a metered network netd
  adds `bw_costly_eth0` legacy chains (Data Saver and quotas only, none set; root's tunnel is
  exempt); a kernel without legacy `mangle` refuses them and nothing is added. PackageManager logs
  that the overlay's SDK (29, what a v1-only signature allows) is below the system's: fine on 13
  and 14, an Android 15 image would need the image's SDK and a v2 signature. Wi-Fi and the GPU:
  [below](#wi-fi-and-the-gpu-what-stays-an-emulators).
- **Not given** (no HAL in the image): NFC and fingerprint (declared, they would come with a
  missing adapter, `isHardwareDetected() == false`, which no real phone shows). The image has no
  sensors HAL either (`dumpsys sensorservice`: `No Sensors on the device`, while its
  `handheld_core_hardware.xml` declares the accelerometer and compass); Telegram reads sensors for
  raise-to-listen, wallpaper parallax and Mini Apps (`BotSensors`). They are given by the HAL
  below when `EMULATOR__SENSORS_HAL_DIR` is set, never through kernel devices (`uinput`, IIO):
  the host kernel is shared by every pod.
- **Sensors** (`core/emulator/_sensors.py`, `docker/sensors-hal`): redroid has no sensors HAL,
  so `SensorManager` finds nothing. With `EMULATOR__SENSORS_HAL_DIR` set, a new or refreshed
  container gets one in `/vendor` before it boots: an AIDL `android.hardware.sensors` service
  (`bin/hw/android.hardware.sensors-service.multihal`, AOSP's multi-HAL name), its init script,
  VINTF fragment (`ISensors/default`, v2), config (`etc/sensors/sensors.conf`) and the
  `android.hardware.sensor.*` features of what it serves. It reports the phone's own chips from
  `configs/oem_sensors/<device>.json` (names, vendors, ranges, delays from public dumps:
  phyphox exports and its sensor DB; a field no dump showed takes its kind's usual value):
  accelerometer, gyroscope, magnetometer, light, proximity (the wake-up default), and gravity,
  linear acceleration and rotation vectors where the phone has them (else Android's own AOSP
  fusion sensors stand in). Readings follow a seed from the serial: held in a hand (tilted
  25–50°, slow drift, 8–12 Hz tremor) or lying on a desk, noise, bias and quantisation of a real
  part, indoor light, proximity far, and the Earth's field (2020 dipole) at the proxy country,
  so a refresh after a move moves it. One worker thread, idle while no sensor is on: ~0% CPU,
  ~5 MB. Tested on the x86_64 13 and 14 `_64only` images (dumpsys, `getDefaultSensor` and
  events from an `app_process` probe, HAL restart); the arm64 build is not yet tested on an
  arm64 host. Build it once on an x86_64 machine (the NDK has no arm64 host) and copy the
  directory to each host: `docker build --output type=local,dest=emulator/sensors-hal
  docker/sensors-hal` writes `arm64-v8a/` and `x86_64/`; the host's Docker architecture picks
  one, and a host without its build boots phones without sensors (a warning). Turned off
  later, a refresh writes inert files over the ones a container got (no service, no features). Every input is
  pinned (NDK r27d and build-tools 34 by sha256, AOSP `android-14.0.0_r75` by commit); two builds
  give the same bytes. The binary holds no emulator names; libc++'s own `generic_category`
  string is in it, as in every phone's `libc++.so`.

### Files copied into a container

`docker cp` of a host path packs it with the host's modes, and from Windows every file arrives
`-rwxr-xr-x`: an executable `build.prop` or init script, which no phone has. Every file a
container is given before boot (`build.prop`, the feature XMLs, the network overlay, the sensors
HAL tree, `cpuinfo.rc`, microG) is packed into a tar here instead (`core/emulator/_archive.py`) and
unpacked with `docker cp -a - <container>:<dir>`, so it lands as the image's own are: files
`0644` root:root, the HAL binary `0755` root:shell, and the /vendor directories it names root:shell
(`bin` and `bin/hw` `0751`), as the image has them (unpacking sets a named directory's owner
and mode, existing ones too). `build.prop` keeps the image's mode, `0644` root:root. Checked on
redroid 14: the files and directories as above, the phone's `ro.product.model` and fingerprint,
nothing in `build.prop` names the app, boot unchanged.

### The phone's hardware name

`ro.hardware` (`Build.HARDWARE`) is what the bootloader passes as `androidboot.hardware`: init
copies `ro.boot.hardware` into it before any `build.prop` is read
([`ExportKernelBootProps`](https://android.googlesource.com/platform/system/core/+/refs/heads/android14-release/init/property_service.cpp)),
so a `build.prop` line cannot change it. redroid's image passes `redroid` in its entrypoint
(`/init qemu=1 androidboot.hardware=redroid`, the same for amd64 and arm64), and a second
`androidboot.hardware` after it loses. A new Android therefore gets `--entrypoint /init` and
`qemu=1 androidboot.hardware=<the phone's>`: `panther` / `shiba` on Pixels (their codename),
`qcom` on Snapdragon phones, `s5eNNNN` on Exynos, `mt6877` on the Redmi
(`configs/oem_blueprints`, `hardware`; `board` and `platform` are its `ro.product.board` and
`ro.board.platform`, from its stock vendor `build.prop`).

The name matters to Android in three places. init imports `/init.${ro.hardware}.rc` and
`/vendor/etc/init/hw/init.${ro.hardware}.rc`: the image has neither (its `init.redroid.rc`
sits in `/vendor/etc/init`, read under any name). ueventd reads `/ueventd.${ro.hardware}.rc`
on images first shipped before Android 13: there is none. The HAL loader
([`hw_get_module`](https://android.googlesource.com/platform/hardware/libhardware/+/refs/heads/android14-release/hardware.c))
tries `ro.hardware.<class>` first, then `ro.hardware`, `ro.product.board`, `ro.board.platform`,
then `default`: gralloc is pinned by the image (`ro.hardware.gralloc=redroid`), the composer
by `ro.hardware.hwcomposer=redroid` in our `build.prop` block (without it SurfaceFlinger
restarts forever, no screen), EGL and Vulkan by `ro.hardware.egl` / `ro.hardware.vulkan`;
audio and the rest find no `<class>.<name>.so` and fall back to `default`, as before.

None of the names is one Telegram's `EmuDetector` looks for (`goldfish`, `ranchu`, `vbox86`,
`nox`, ...), which a test keeps. A container made before keeps `ro.hardware=redroid` until it is
made again (its entrypoint is fixed at create), but its next cold boot reads the rewritten
`build.prop`: from then on its `ro.product.board` and `ro.board.platform` are the phone's, next
to `redroid` as its hardware (its composer still found by that name). Still `redroid` after
this: `ro.hardware.gralloc`, `ro.hardware.hwcomposer`, the `ro.boot.redroid_*` boot arguments
and the HAL file names under `/vendor/lib64/hw`.

## Wi-Fi and the GPU: what stays an emulator's

Measured on redroid 13 and 14 (`_64only`, guest GPU) and read in AOSP; neither is sent to
Telegram's servers (`initConnection` carries `device_token`, `data`, `installer`, `package_id`,
`tz_offset`, `perf_cat`), but any app and a Mini App's WebView can read both.

**Wi-Fi.** `cmd wifi status` says Wi-Fi is disabled, and `wificond` logs `Failed to get NL80211
family info`: Android's Wi-Fi needs a cfg80211 (nl80211) interface from the kernel, and the
image's Wi-Fi HAL and `wificond` have none. The two ways to give one are kernel modules, host-wide
and shared by every pod and the server itself, so neither is done:

- `mac80211_hwsim`, redroid's old virtual Wi-Fi ([redroid-doc#10](https://github.com/remote-android/redroid-doc/issues/10)):
  its radios are made in the host's namespace and need an access point (hostapd) in the pod;
- `virt_wifi`, Cuttlefish's ([redroid-doc#791](https://github.com/remote-android/redroid-doc/issues/791)):
  it wraps eth0 into a wlan0 (eth0 itself stops carrying traffic, which the kill-switch and adb
  rules are written for) and names its network `VirtWifi`, Cuttlefish's own SSID.

Labelling eth0 `TRANSPORT_WIFI` with the overlay above is possible but not offered: `WifiManager`
would still say Wi-Fi is off and give no `WifiInfo`, a contradiction any app reads without a
permission. A real mobile data connection (a radio HAL, `IRadio` AIDL, answering
`setupDataCall` with the pod's interface, as Cuttlefish's RIL and modem simulator do) needs no
kernel either, but is a project of its own: every radio service (SIM, network, data, modem,
voice, messaging) would have to answer as a real modem does.

**GPU.** `guest` draws with ANGLE on SwiftShader (`ro.hardware.egl=angle`,
`ro.hardware.vulkan=pastel`); `dumpsys SurfaceFlinger` reports `Google Inc. (Google)`,
`ANGLE (Google, Vulkan 1.2.0 (SwiftShader Device (LLVM 10.0.0) (0x0000C0DE)), SwiftShader
driver-5.0.0)`, `OpenGL ES 3.1.0 (ANGLE 2.1.1 ...)` (Vulkan 1.3.0 on 14). Telegram reads the
strings only for its shader cache key and a debug log; WebGL in a Mini App sees them. No phone
reports a software renderer, and no catalogue phone's GPU is a server's: Ampere and Graviton
have none, and an RK3588 in `host` mode reports Mesa's `Mali-G610 (Panfrost)` and an `OpenGL ES
... Mesa` version, a GPU no catalogue phone has (Pixel 7/8 Mali-G710/G715, Snapdragon Adreno
725/740, Exynos Mali-G68 and Xclipse 530/940, Dimensity 1080 Mali-G68) and a driver no phone
ships. Rewriting only the strings (a GLES layer, which needs `enable_gpu_debug_layers` and
`gpu_debug_app` in global settings any app reads, or a patched ANGLE) would leave SwiftShader's
extensions, limits and speed under a phone's name, so it is not done. A phone's renderer needs a
phone's GPU: hosts with the GPU of the phones they present, the operator's call.

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

The seeded contacts never leave the phone: before every start of the app, Telegram's contacts
group (`READ_CONTACTS`, `WRITE_CONTACTS`, `GET_ACCOUNTS`) is revoked and flagged
`user-set user-fixed` ("denied, don't ask again"), so Android answers Telegram's request itself
with no dialog. Checked on redroid 13 and 14: the flags stick, and a request then opens
`GrantPermissionsActivity` that closes by itself within a second. Telegram's own prompt before
that request is answered «not now»; a permission dialog that shows anyway is denied by its
`permission_deny_button` resource-id, never by its words.

## A sign-in that meets a frozen app

On redroid's guest GPU, Telegram can deadlock right after «Start Messaging» (about 1 in 5 fresh
pods): the app stays alive but every `uiautomator dump` fails ("null root node") and any key
kills it. A dump that cannot be taken is therefore `unresponsive`, never an empty screen: the
sign-in force-stops and relaunches the app once and starts again from its entry screen (a new
login code only as the per-account pacing allows), and refuses with
`emulator_app_unresponsive` when it freezes again. A screen nothing recognises after a few BACKs
is `emulator_screen_unknown`. Both reach the operator's dialog as the reason, and warming as
passing causes; only a sign-in screen reads as signed out.

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

## A warming visit on the phone

A visit (`services/warming/_mobile_cycle.py`, its plan in `_mobile_plan.py`) reads the channels
the phone's chat list shows, reacts with the persona's chance while reactions are on, glances at
stories (never on a «Глянул» visit), looks at photos, plays voice and video notes only while the
`video` or `voice` extra is on, and searches only while the `search_messages` extra is on, with a
word of the phone's language from `EMULATOR__SEARCH_QUERIES_BY_LANGUAGE`, else
`EMULATOR__SEARCH_QUERIES` (`input text` types ASCII only, so a Cyrillic phone searches in
English). It never joins, writes a DM or runs other extras: a phone stint is nobody's dialogue
partner and never draws a reply-only visit, and its card shows only the steps the app takes.

It books the day's budget as desktop warming would: a channel read and a reaction each, one story
glance however many stories it showed, each note played; a search or a photo books nothing. A
Stop or restart mid-session keeps what the phone already did spent. Telegram's limit dialog in
the app is a `flood_wait` of unknown length (desktop's fallback hours and margin); an app that
stays offline (`emulator_app_offline`), a screen it cannot read (`emulator_field_missing`) and a
screen that does not open are retryable failures, retried up to the failure limit. A full host
retries the visit in 5-15 minutes. A phone whose visit found no channel to read says so on its
card until a visit reads one; a visit that ran in the app clears a failed preparation's note.

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

Servers without a GPU (Ampere Altra, Graviton) stay on `guest`, whose renderer is software ([Wi-Fi and the GPU](#wi-fi-and-the-gpu-what-stays-an-emulators)). On an RK3588 board `/dev/dri`
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

One base per SDK in the host's images (`EMULATOR__REDROID_IMAGES`, `_ARM64` on ARM64), made by the operator from a phone that has booted
once and done nothing else: never started by the app, so it holds no Telegram, contacts, photos or
seeding marker. On the Docker host, as root (`34` and its image; repeat for `33`):

```bash
SDK=34 IMAGE=redroid/redroid:14.0.0_64only-latest ROOT=/srv/telebuba  # amd64: 14.0.0-latest
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
all of them built in. Build in what the gateway and Docker's `--publish` use too: DNAT,
MASQUERADE, REDIRECT, `xt_conntrack`, `xt_addrtype`, the mangle tables, TTL/HL, `nft_compat` and
TUN. As modules from `modules.vhdx` they load on demand, and a WSL VM where that load fails leaves
every DNAT rule failing ("Extension DNAT revision 0 not supported"): no published port, no gateway.
The kernel still has no `xt_mark`, so the kill-switch matches addresses, not marks. Inside the
Ubuntu distro (as root for the packages, as the user for the build):

```bash
apt-get install -y build-essential flex bison dwarves libssl-dev libelf-dev bc python3 cpio kmod qemu-utils git
git clone --depth 1 --branch linux-msft-wsl-6.6.87.2 https://github.com/microsoft/WSL2-Linux-Kernel.git ~/WSL2-Linux-Kernel
cd ~/WSL2-Linux-Kernel && cp Microsoft/config-wsl .config
scripts/config --enable ANDROID_BINDER_IPC --enable ANDROID_BINDERFS \
  --set-str ANDROID_BINDER_DEVICES "binder,hwbinder,vndbinder" --disable LOCALVERSION_AUTO \
  --enable IP_NF_IPTABLES --enable IP_NF_FILTER --enable IP6_NF_IPTABLES --enable IP6_NF_FILTER \
  --enable NETFILTER_XT_TARGET_HL --enable IP_NF_TARGET_TTL \
  --enable NETFILTER_XT_NAT --enable IP_NF_NAT --enable IP6_NF_NAT \
  --enable NETFILTER_XT_TARGET_MASQUERADE --enable IP_NF_TARGET_MASQUERADE \
  --enable IP6_NF_TARGET_MASQUERADE --enable NETFILTER_XT_TARGET_REDIRECT \
  --enable IP_NF_TARGET_REDIRECT --enable NETFILTER_XT_MATCH_CONNTRACK \
  --enable NETFILTER_XT_MATCH_ADDRTYPE --enable NFT_COMPAT --enable IP_NF_MANGLE \
  --enable IP6_NF_MANGLE --enable TUN
make olddefconfig && make -j"$(nproc)"
rm -rf modules modules.vhdx  # a rebuild: stale .ko files, and the script never overwrites
make INSTALL_MOD_PATH="$PWD/modules" modules_install
sudo ./Microsoft/scripts/gen_modules_vhdx.sh "$PWD/modules" "$(make -s kernelrelease)" modules.vhdx
mkdir -p /mnt/c/wsl-kernel/new && cp arch/x86/boot/bzImage modules.vhdx /mnt/c/wsl-kernel/new/
```

The first time, with no kernel in `C:\wsl-kernel` yet: point WSL at it in
`%UserProfile%\.wslconfig`, `wsl --shutdown`, move both files from `C:\wsl-kernel\new` up into
`C:\wsl-kernel`, and start Docker Desktop (a rebuild swaps them as below instead):

```ini
[wsl2]
kernel=C:\\wsl-kernel\\bzImage
kernelModules=C:\\wsl-kernel\\modules.vhdx
```

Check: `wsl -d Ubuntu -- grep binder /proc/filesystems` prints a `binder` line. To undo, remove
the two lines and `wsl --shutdown`. WSL updates no longer bring kernel fixes: rebuild with the
new tag when Microsoft ships one. A running WSL holds `modules.vhdx` open, so a rebuild stages
both files in `new`, keeps the old pair, and swaps them in with WSL stopped (PowerShell):

```powershell
$ErrorActionPreference = 'Stop'  # a failed backup stops here, before WSL goes down
$k = 'C:\wsl-kernel'; $old = "$k\backup-$(Get-Date -Format yyyy-MM-dd-HHmmss)"; $old
New-Item -ItemType Directory -Force $old | Out-Null
Copy-Item "$k\bzImage", "$k\modules.vhdx" $old
docker desktop stop --timeout 60; if ($LASTEXITCODE) { throw 'docker desktop stop failed' }
wsl --shutdown; if ($LASTEXITCODE) { throw 'wsl --shutdown failed' }
Move-Item -Force "$k\new\bzImage", "$k\new\modules.vhdx" $k
```

Start Docker Desktop from the Start menu (over SSH, `docker desktop start` runs it in the SSH
session, which takes it down on logout). To roll back, stop Docker Desktop and WSL the same way,
copy both files back from the backup folder the swap printed (in a new PowerShell, name it:
`$old = 'C:\wsl-kernel\backup-…'`) with `Copy-Item -Force "$old\bzImage", "$old\modules.vhdx"
C:\wsl-kernel`, and start it again.

Then `winget install Genymobile.scrcpy` (for its `adb`). The images need nothing by hand: before a
pod's first start (`core/emulator/images.py`) a missing redroid image for the phone's SDK is pulled,
and a missing `EMULATOR__GATEWAY_IMAGE` is built from `docker/net-gateway` (both at once), each bounded by
`EMULATOR__IMAGE_TIMEOUT_SECONDS` (`emulator_image_unavailable` when it fails). An image already
present is never pulled or rebuilt; after changing `docker/net-gateway`, bump the tag. A pod made
before gets the new image at its next start: a gateway made from another image (compared by image
ID) is removed and made again at the pod's index, and its Android with it, using its existing diff.

## Host setup (Linux ARM64)

The production hosts: Ubuntu or Debian on arm64 (Ampere Altra, AWS Graviton, RK3588 boards), the
app and Docker on the same machine, ext4 under the shared /data directories. The images are the
`*_64only` tags (`EMULATOR__REDROID_IMAGES_ARM64`): Ampere and other cores without 32-bit EL0
cannot run a 32-bit userspace, and Telegram's universal APK carries `arm64-v8a`. On x86_64 the
13.0.0 `_64only` tag lists no video or audio encoder at all (no IOmxStore; Codec2's software
encoders do not reach `MediaCodecList`), so scrcpy-server dies at once and the web screen reads
`screen_ended`; amd64 hosts therefore keep the `-latest` tags. Whether the arm64 `_64only` images
have an encoder is not yet measured.

```bash
apt-get install -y docker.io adb
# Docker Engine 20.10 (API 1.41) or newer: pods are created with --cgroupns private.
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
`androidboot.use_memfd=true` replaces ashmem, which these kernels no longer have. `/dev/fuse` must exist
(`CONFIG_FUSE_FS`; built in on stock Ubuntu and Debian): a software-drawing pod is given it.

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
`org.telegram.messenger` APK keeps its app and its sign-in. Which one a phone has is read
(`pm list packages`) once per container run: again after an install and at every container start,
since the next container at the pod's index (and adb serial) may be another phone.

Each screen read is one `uiautomator dump` (`core/emulator/adb.py`). On Docker Desktop/WSL2 it
costs about 2 s: about 0.1 s VM start, then uiautomator's fixed 1 s wait for an idle screen, a few
milliseconds of fetching, and about 0.8 s of VM shutdown after the file is written. The dump script
kills uiautomator at its "dumped to" print, so a read takes about 1.25 s. The idle wait is
hardcoded in uiautomator, and only a long-lived dump server of our own on the phone could
shorten it.

## Google apps (microG)

A phone has Play services, its Services Framework and the Play Store as privileged system apps
in `/product/priv-app`; redroid's images have none. A new container gets microG's builds of the
three under a Pixel's names (`core/emulator/_microg.py`), copied into /product before its first
boot like the other files (Files copied into a container), and PackageManager installs them as
system apps at that boot:

| /product/priv-app/ | Package | microG's file |
|---|---|---|
| `PrebuiltGmsCore` | `com.google.android.gms` | GmsCore `com.google.android.gms-252432034.apk` (0.3.17.252432) |
| `Phonesky` | `com.android.vending` | Companion (FakeStore) `com.android.vending-84022634.apk` |
| `GoogleServicesFramework` | `com.google.android.gsf` | GsfProxy `GsfProxy.apk` (v0.1.0) |

The files are the ones microG publishes (microg.org/download.html leads to its GitHub
releases), the plain builds, not `-hw` (Huawei) or `org.microg.gms` (no `com.google` name). They
are downloaded into `EMULATOR__MICROG_DIR` (at startup and when a container needs them) and kept
only when their sha256 is the one pinned in code, so a new microG version is a code change. All
three are signed with microG's key (SHA-256 `9bd06727…d14165`, the microG F-Droid repository's
fingerprint). PackageManager does not unpack a system app's native libraries: GmsCore's for the
host's ABI go beside it, `lib/x86_64/` (`lib/arm64/` on ARM64). Their privileged permissions
need no allowlist: the images set `ro.control_privapp_permissions=disable`. Checked on redroid
13 and 14: the three are `SYSTEM PRIVILEGED PRODUCT` apps, GmsCore's `primaryCpuAbi` is
`x86_64` and its privileged permissions are granted, `com.google.android.gms` and its
`:persistent` process run without a crash, the copy takes about 6 s and boot is unchanged.
microG's own defaults are kept: its cloud messaging stays off ("gcm is disabled" at boot) until
turned on in its settings.

A container keeps what it was given. One made before gets microG at its next start, as does one
whose download failed (the phone is made all the same, without it). An empty
`EMULATOR__MICROG_DIR` gives new containers none and leaves the ones that have it alone.

An app's Play services library checks that `com.google.android.gms` is signed by Google before
it uses it. microG is signed with its own key, so without the patch below the library finds the
wrong signature and treats Play services as unavailable (Telegram, for one, then gets no FCM
push through it). `EMULATOR__FRAMEWORK_PATCH_DIR` turns that patch on (next).

### Signature spoofing

So apps accept microG, the framework is made to report Google's own certificate for microG's
three packages. This is the patch LineageOS-for-microG, /e/OS and CalyxOS ship, built here
against a redroid image instead of from source (`docker/framework-patch`, `core/emulator/_microg.py`
has the certificate; SHA-256 `f0fd6c5b…db83`, Google's Play services signing cert):

- `ComputerEngine.generatePackageInfo` (in `/system/framework/services.jar`) is where Android
  builds the `PackageInfo` an app reads. A tiny `FakeSig` helper and one call to it are injected
  right after it: for `com.google.android.gms`, `com.android.vending` and `com.google.android.gsf`
  the `signatures` field is replaced with Google's certificate, every other package untouched.
  No new permission, so nothing else on the phone can ask to be spoofed; the three are a fixed
  allowlist in the helper (the `FAKE_PACKAGE_SIGNATURE` approach would need `framework-res` patched
  too and a wider surface).
- The patch is built per SDK against that SDK's image, `services.jar` disassembled with baksmali,
  the helper added, reassembled with smali and the jar repacked (both pinned by sha256):

  ```
  docker build --build-context redroid=docker-image://redroid/redroid:14.0.0_64only-latest \
    --output type=local,dest=emulator/framework-patch/34 docker/framework-patch
  docker build --build-context redroid=docker-image://redroid/redroid:13.0.0_64only-latest \
    --output type=local,dest=emulator/framework-patch/33 docker/framework-patch
  ```

  (on an ARM64 host, against that host's ARM64 images). Needs BuildKit for `--build-context`.
- A new or refreshed container (`give_spoofing`) gets `<dir>/<sdk>/services.jar` copied over the
  image's, and its precompiled `services.odex`/`.vdex`/`.art` overwritten with a stub so ART runs
  the patched dex. A marker records it, so a refresh patches a container only once (reapplying
  would force a slow `dex2oat` at every boot). Empty `EMULATOR__FRAMEWORK_PATCH_DIR`, no ABI of
  ours, or no patch built for the SDK: nothing is copied and apps see microG's own signature.
- Checked on redroid 13 and 14: the patch builds, the container boots (~9 s, system_server
  healthy), and the injected helper runs in system_server for exactly the three packages, setting
  Google's certificate. Not checked: an ARM64 host, and an app's end-to-end push.

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
  (`manual`) and excludes a warming visit, and the other way round. A phone the operator opened
  (Start, or the account's phone button) is theirs as well: warming skips it until they put it to
  sleep or end a `control` session on it (with `EMULATOR__DESKTOP_VIEWER`, until its window closes).
  A visit that ends while the operator opens or holds its phone leaves it to them, neither paused
  nor stopped.
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
it is, never updated. Another version needs its own hash in that setting. The API lists a phone's
apps (`platform`), so other platforms can join it later.

### Independent emulators

**+ Добавить независимый эмулятор** on the same page makes a phone bound to no account
(`services/device_farm/_standalone.py`, table `standalone_emulators`): a fresh Android identity and
one pool proxy, picked from the pool (or added there in place, as on the Accounts page). It takes a
slot on that proxy like an account does. Its container is `EMULATOR__STANDALONE_PREFIX` + its id
(`telebuba_sa-…`, never under `EMULATOR__INSTANCE_PREFIX`, so it never reads as an account's). Start
boots the same pod on its current SOCKS5 proxy with that proxy country's timezone and SIM, installs
the apps ticked when it was added («Установить по умолчанию»: Telegram, ticked by default, and
Госуслуги) when the phone lacks them, seeds the first boot, and signs nothing in: the operator works
it on the web screen. Phones added before the choice existed install Telegram.

Госуслуги (`core/emulator/_gosuslugi.py`, package `ru.rostel`) publishes no APK of its own: its
current universal build comes from RuStore's web API (`overallInfo`, then `v2/download-link`),
looked up and downloaded through the phone's own proxy, since RuStore's CDN crawls from abroad. It
lands in `EMULATOR__GOSUSLUGI_APK_PATH` and is kept only when signed with the pinned Госуслуги
certificate. A download that stalls for a minute is resumed where it stopped (`Range`), a few times
at most; `EMULATOR__APP_DOWNLOAD_TIMEOUT_SECONDS` bounds the whole file.
Changing or detaching its proxy (its Параметры), or deleting the proxy, puts it to sleep; deleting
the phone removes its containers, its `/data` diff and its row. Warming never touches it.

## Settings

```env
EMULATOR__ENABLED=true
# The adb next to scrcpy.exe: scrcpy starts its own adb, and two versions kill each other's server.
EMULATOR__ADB_PATH=C:/Users/<you>/AppData/Local/Microsoft/WinGet/Packages/Genymobile.scrcpy_Microsoft.Winget.Source_8wekyb3d8bbwe/scrcpy-win64-v4.1/adb.exe
EMULATOR__TELEGRAM_APK_PATH=emulator/telegram.apk  # fetched from telegram.org when missing
EMULATOR__GOSUSLUGI_APK_PATH=emulator/gosuslugi.apk  # fetched from RuStore when missing
```

`EMULATOR__CPU_CORES` (phones without a chip) and `EMULATOR__MEMORY_MB` size new containers;
the screen is the phone's.
`EMULATOR__BATTERY_DIR` holds each pod's battery tree (Identity and region, Battery).
`EMULATOR__SENSORS_HAL_DIR` (empty: off) is the sensors HAL's build output (Identity and region,
Sensors).
`EMULATOR__MICROG_DIR` (empty: off) holds microG's APKs (Google apps (microG)).
`EMULATOR__FRAMEWORK_PATCH_DIR` (empty: off) holds the built signature-spoofing patch, one
`<sdk>/services.jar` per SDK (Signature spoofing).
`EMULATOR__NETWORK_TRANSPORT` (`cellular` or `ethernet`) is the network Android reports for eth0
(Identity and region, Network).
