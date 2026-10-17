# redroid: the mobile container under Docker

The mobile container is a [redroid](https://github.com/remote-android/redroid-doc) Android
running under Docker (`core/emulator/redroid.py`); the flow is `services/accounts/mobile_emulator.py`.
The container presents the mobile fingerprint's maker, model and SDK, stays offline until the
Telegram app's own SOCKS5 setting names the account's proxy, and is signed in through `adb`.
Docker has no window, so the phone is shown in a scrcpy window opened by the phone button.

## How a container is run

- **Found again** by name (`EMULATOR__INSTANCE_PREFIX` + account id) among containers labelled
  `telebuba.emulator`; its **index** is the `telebuba.index` label, the lowest free one at create.
- **adb** is published on `127.0.0.1:(EMULATOR__REDROID_FIRST_ADB_PORT + index)` only.
- **Identity**: `ro.product.manufacturer` / `ro.product.model` in a `build.prop` copied to
  `/system_dlkm/etc/` before the first boot (not on the command line: redroid's init re-splits it
  on spaces, so `Pixel 7` would become `Pixel`); the image is the one
  `EMULATOR__REDROID_IMAGES` names for the fingerprint's SDK. redroid has no telephony, so the
  generated IMEI is not given to the container.
- **Offline first start**: `docker exec` adds an iptables chain dropping all output except loopback
  and adbd's replies, before Android is up; it is removed once the app's proxy is set. It cannot
  land before `docker start` (the image runs nothing before init, and a network attached once
  netd owns routing gets no gateway), so it must land before zygote, and fails otherwise: here
  `docker exec` works ~0.3 s after start and zygote runs at ~1.4–2 s.
- **Window**: `scrcpy --serial … --window-title <container>`, one per container.

A container keeps its `/data` across stop/start; deleting the account runs `docker rm --force`.

## Host setup (Windows, Docker Desktop on WSL2)

redroid needs binder in the kernel, and the offline switch needs the legacy iptables filter
tables Android's own `iptables` uses. The stock WSL2 kernel has `CONFIG_ANDROID_BINDER_IPC` unset
and the filter tables only as modules nobody loads, so build Microsoft's kernel of the same
version with all of them built in. Inside the
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

Then `winget install Genymobile.scrcpy` and pull the images named in `EMULATOR__REDROID_IMAGES`.

## Settings

```env
EMULATOR__ENABLED=true
# The adb next to scrcpy.exe: scrcpy starts its own adb, and two versions kill each other's server.
EMULATOR__ADB_PATH=C:/Users/<you>/AppData/Local/Microsoft/WinGet/Packages/Genymobile.scrcpy_Microsoft.Winget.Source_8wekyb3d8bbwe/scrcpy-win64-v4.1/adb.exe
EMULATOR__TELEGRAM_APK_PATH=emulator/telegram.apk  # an x86_64-capable APK: redroid has no ARM translation
```

`EMULATOR__RESOLUTION`, `EMULATOR__CPU_CORES` and `EMULATOR__MEMORY_MB` size new containers.
