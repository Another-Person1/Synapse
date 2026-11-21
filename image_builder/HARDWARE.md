# Appliance networking and board interfaces

This image preinstalls and starts Synapse automatically. Its default dashboard is
**http://synapse.local:3000**. Complete Armbian's initial account setup and configure
cameras, pipelines and the team before use. The default hostname is `synapse`;
Avahi publishes mDNS and HTTP service discovery on available network interfaces.
If another device already owns that name, Avahi may use a collision suffix.
The client computer must support mDNS, and the network must permit multicast.

## Shared Raspberry Pi image

The artifact is named `synapse-<version>-raspberrypi-armbian-<release>.img.xz`.
Select `raspberrypi` in GitHub Actions. CLI aliases `rpi4b`, `raspberrypi5`, `cm4`
and `cm5` resolve to the same image target. Armbian still calls the upstream base
`rpi4b`; that upstream identifier is retained for authenticity and board checks.

The authenticated Armbian 26.8.1 base contains Pi 4, Pi 5, CM4, CM4 IO, and CM5
CM4IO/CM5IO device trees. Builds check for all four requested models and reject
an upstream base missing a required tree. The same ARM64 kernel and firmware
are retained. The builder mounts the actual FAT firmware partition before
changing its configuration or running package hooks.

CM4 and CM5 support depends on the carrier's wiring, firmware and boot media.
An image cannot add missing USB routing, camera connectors or power circuitry.
Use the vendor's `rpiboot` procedure to expose eMMC before flashing eMMC models;
Lite models use the carrier's supported storage. Bootloader/EEPROM updates and
custom carrier device-tree changes remain separate from flashing this image.
No firmware is flashed by this builder. Hardware boot/camera testing on each
module and carrier remains required before describing them as qualified.

## Ethernet and USB-C

| Board | USB-C Ethernet gadget | Connection |
| --- | --- | --- |
| Raspberry Pi 4B | Configured | USB-C power/data connector |
| Raspberry Pi 5 | Configured | USB-C power/data connector |
| CM5 | Configured for the official IO board | Carrier USB-C with USB2 device signals routed |
| CM4 | Configured, assuming a USB-C carrier | Carrier must route the module USB2 device signals to USB-C |
| Orange Pi 5 | Configured | Data-capable USB-C, not the separate power input |
| Orange Pi 5 Plus | Configured | Data-capable USB-C, not the separate power input |
| ROCK 5B | Configured | Data-capable USB-C |
| ROCK 5C | Disabled | USB-C is power input; its USB-A OTG port is excluded |
| Dragon Q6A | Disabled | USB-C is power input; its USB-A OTG port is excluded |
| Dragon Q8B | Disabled | Both USB controllers are host-only |
| Cubie A7Z | Disabled | USB-C is host-only (OTG broken); use WiFi 6 or Ethernet |

Only board-side USB-C device connections are supported. A standard host USB-A
to board USB-C **data** cable is acceptable. USB-A to USB-A gadget connections
are not implemented. Use a power arrangement supported by the board/carrier;
a computer port may not supply enough power for the board and its cameras.
USB-C gadget mode reserves that connector for device operation. Connect host
peripherals and USB Ethernet adapters to remaining supported host ports.
On Orange Pi 5 vendor kernels, the conflicting Armbian USB host-forcing service
is masked. Kernel adapters and other ports retain their host support.

The gadget is a configfs composite device (`1d6b:0104`) built by
`synapse-usb-ethernet`. Configuration 1 is RNDIS with Microsoft OS descriptors,
so Windows 10/11 installs its built-in RNDIS driver automatically. Configuration
2 is CDC-ECM. The legacy `g_ether` module is not used, because it sends no OS
descriptors and Windows binds it as a "USB Serial Device (COMx)" with no network
adapter. Device and host MAC addresses and the USB serial number are derived from
the per-device machine ID, not baked into the image. Each configuration has its
own kernel `usbN` link. `05-synapse-usb-gadget.network` matches both by driver
and sorts ahead of netplan's generated files; only the link the host activates
has carrier and is configured.
The coprocessor has **10.12.194.1/28**, and a DHCP server restricted to that link
assigns host addresses. Open http://10.12.194.1:3000 if mDNS is unavailable.
This link does not advertise a default router or DNS server, bridge the robot
network, or enable Internet sharing. Multiple gadgets using this same subnet
on one computer require assigning separate subnets. macOS selects configuration
1 (RNDIS), which it does not support, so it is not a supported USB-C host.

A missing USB device controller is reported in the service journal and does not
prevent the runtime from starting. A registered UDC and valid DTB are structural
checks; cable orientation, host drivers, power and actual enumeration still need
physical testing, including connection after boot.

Built-in Ethernet and supported USB Ethernet adapters use Armbian's DHCP
configuration, with IPv4/IPv6 link-local addressing available for direct links.
Adapters still require their drivers in the selected Armbian kernel. They do not
receive the gadget's DHCP-server configuration. Display current addresses using
`ip address`. The image does not assume a particular physical Ethernet port.

### Compute Module carrier assumptions

CM4 and CM5 default to USB2 device mode, assuming the carrier routes that
controller to USB-C. This uses the modules' standard controller rather than a
carrier-name allowlist. The image cannot infer connector routing or guarantee
power negotiation on an arbitrary carrier. The official CM4 IO board has
micro-USB rather than USB-C; its connector is outside the USB-C support scope.

CM4's `otg_mode=0` plus `dtoverlay=dwc2,dr_mode=peripheral` supersede Armbian's
host-mode default. A carrier that needs that controller for host peripherals
must remove the managed CM4 `dwc2` device overlay and restore `otg_mode=1` in
`/boot/firmware/config.txt`. USB2 host and gadget roles cannot coexist on the
same controller. A carrier with independent host controllers can keep using
those for cameras/adapters while this controller provides gadget Ethernet.

## SPI, GPIO, PWM and UART

The table uses **physical header pin numbers**, not Linux GPIO offsets.
An enabled alternate function reserves its pins; those pins cannot also be used
as general GPIO or another bus. Other available GPIO lines remain accessible
through the GPIO character-device interface.

| Hardware | SPI header pins | UART header pins | PWM header pins |
| --- | --- | --- | --- |
| Pi 4 / CM4 | 19, 21, 23, 24, 26; SPI0 | 8, 10; primary UART | 32, 33; hardware PWM, GPIO12/13 |
| Pi 5 / CM5 | 19, 21, 23, 24, 26; SPI0 | 8, 10; UART0 through RP1 | 32, 33; hardware RP1 PWM0, GPIO12/13 |
| Orange Pi 5 | 19, 21, 23, 24; SPI4 M0 CS1 | 8, 10; UART0 M2 | 16; hardware PWM1 M1 |
| Orange Pi 5 Plus | 19, 21, 23, 24; SPI0 M2 CS0 | 8, 10; UART6 M1 | 33; hardware PWM14 M0 |
| ROCK 5B | 19, 21, 23, 24, 26; SPI0 M2 CS0/CS1 | 8, 10; UART2 M0, normally `/dev/ttyS2` | 32; hardware PWM14 M0 |
| ROCK 5C | 19, 21, 23, 24; SPI4 M2 CS0 | 8, 10; UART2 M0, normally `/dev/ttyS2` | 13; hardware PWM14 M1 |
| Dragon Q6A | 19, 21, 23, 24; SPI12 CS0 | 8, 10; UART5, normally `/dev/ttyMSM0` | 5; **software PWM** using GPIO25 |
| Dragon Q8B | 19, 21, 23, 24; SPI20 CS0 | 8, 10; UART17, normally `/dev/ttyMSM0` | 5; **software PWM** using GPIO42 |
| Cubie A7Z | 19, 21, 23, 24; SPI1 (enabled in the base tree) | 7, 11; UART2 | 36; hardware PWM0 on PB4 |

Q6A and Q8B have no hardware PWM signal in Radxa's published 40-pin mux table. Their
shipped kernels provide the upstream `pwm-gpio` driver, which this image exposes on
pin 5. It is software timed and can jitter under load. It is unsuitable where precise
pulse timing is required. No PWM waveform or frequency is started by provisioning;
applications choose the period, duty cycle and enabled state through the PWM API.
Inspect `/sys/class/pwm` to identify controllers rather than hard-coding chip numbers.

### ROCK 5B

The ROCK 5B routes its data USB-C through DWC3 controller 0 in peripheral mode
(the same `synapse-usb-c` overlay as the Orange Pi boards). Header pins 8/10 are
UART2 M0, which the vendor kernel otherwise uses for its `ttyFIQ0` debug console.
The official `rk3588-uart2-m0` overlay disables that FIQ debugger and exposes the
pins as a normal `/dev/ttyS2`; the image pins the kernel console to the display
(`console=display` in `/boot/armbianEnv.txt`) and masks the `ttyS2`/`ttyFIQ0`
serial gettys so the header UART belongs to applications. U-Boot still prints
its boot log on these pins before Linux starts.
SPI0 M2 (pins 19/21/23/24/26) and PWM14 M0 (pin 32) are enabled with the official
`rk3588-*` overlays.

### ROCK 5C

The ROCK 5C build pins Armbian's `vendor` kernel branch (`"branch": "vendor"` in
`boards.json`), the same Rockchip 6.1 tree as the other RK3588 boards, so its
official `rk3588-*` overlay names apply; Armbian's mainline `current` image uses
a different overlay set. Header pins 8/10 are UART2 M0 and are handled exactly like
the ROCK 5B (`/dev/ttyS2`, kernel console on the display, gettys masked). SPI4 M2
(pins 19/21/23/24, CS0) and PWM14 M1 (pin 13) use the official
`rk3588-spi4-m2-cs0-spidev` and `rk3588-pwm14-m1` overlays. The USB-C port is a
power input and the board tree sets the OTG controller to host mode behind a
USB-A port, so USB gadget networking is disabled.

### Dragon Q8B

The Q8B boots through GRUB with a per-kernel DTB (`/boot/armbian-dtb-*`), like the
Q6A. SPI20 (GPIO87-90, header pins 19/21/23/24, CS0 only) is disabled by default, so
the compiled `synapse-q8b-spi` overlay enables it with the `qup_spi20_data_clk` and
`qup_spi20_cs` pinctrl groups and adds the userspace SPI child node. Header pins
8/10 are UART17 (`/dev/ttyMSM0`); the image strips it from the kernel command line
via GRUB and masks `serial-getty@ttyMSM0.service`. Both USB controllers are
host-only, so USB-C gadget networking is unavailable on this board.

### Cubie A7Z

The Cubie A7Z has no recommended Armbian image, so its build opts into the
community (`csc`) trixie vendor image (`allow_community` in `boards.json`); it is
still checksum- and signature-verified. The board uses an MBR partition table.
SPI1 on header pins 19/21/23/24 is already enabled with `spidev` in the base
device tree, so no SPI overlay is needed. UART2 (header pins 7/11) and PWM0
(pin 36) are enabled with the official `sun60i-a733-uart2` and
`sun60i-a733-pwm0` overlays through `overlays=` in `/boot/armbianEnv.txt`. The
console stays on UART0 (`/dev/ttyS0`, pins 8/10). The USB-C port is host-only
(OTG is broken upstream), so USB-C gadget networking is unavailable; the board
networks over its WiFi 6.

Header I2C is **not enabled by Synapse**. Existing internal I2C controllers for
power management, displays and other board functions are preserved. On Q6A,
SPI12 is enabled while its mutually exclusive I2C12/UART12 controllers remain
disabled. GPIO25 is assigned to software PWM instead of I2C6. Board interfaces
are not interchangeable solely because headers have a similar physical layout.

The `synapse` account belongs to `gpio`, `spi` and `dialout`. Narrow udev rules
grant group access to GPIO chips, SPI devices and exported PWM controls; devices
are not made world-writable. Header UART login consoles are disabled on Raspberry
Pi, the Q6A/Q8B (`ttyMSM0`) and the ROCK 5B/5C (`ttyS2`). The separate Orange Pi
debug console is retained. Raspberry Pi
Bluetooth serial attachment and analogue audio are disabled to free its UART and
PWM resources. Camera auto-detection and the Pi 5 cooling-fan controller are retained.

The image includes `gpiod` tools and official `python-periphery` 2.5.0 in the
runtime virtual environment, exposing `GPIO`, `SPI`, `PWM` and `Serial` APIs.
Use `gpioinfo`, `ls /dev/spidev*` and `ls /sys/class/pwm` after boot to discover
actual devices. The kernel-update hooks restore the RP1 overlay after Armbian's
firmware refresh and reapply the Q6A/Q8B SPI/software-PWM configuration to their
GRUB DTBs.

## Validation and provenance

Each image records its hardware profile, default URL and PWM implementation in
`/usr/share/synapse-image/hardware.json`, alongside dependency inventories.
Provenance includes the hardware provisioning code and requirements hashes.
New overlays are compiled and applied against the selected image's actual trees;
missing overlays, symbols or kernel facilities stop the build. Repeated provisioning
was checked against boot files from all four authenticated official bases.
Netplan generation, udev syntax, shell scripts and root-user permissions were checked.
Firmware, root filesystems, partition tables and final XZ archives are checked
before publishing an artifact. These checks detect structural errors and corruption;
they cannot prove physical peripheral operation, electrical safety or timing.

Official sources used:

- [Armbian Raspberry Pi board configuration](https://github.com/armbian/build/blob/main/config/boards/rpi4b.conf)
  and [firmware configuration and update hooks](https://github.com/armbian/build/blob/main/config/sources/families/bcm2711.conf).
- [Raspberry Pi USB gadget project](https://github.com/raspberrypi/rpi-usb-gadget).
- [Raspberry Pi firmware model filters](https://github.com/raspberrypi/documentation/blob/develop/documentation/asciidoc/computers/config_txt/conditional.adoc)
  and [Compute Module flashing](https://github.com/raspberrypi/documentation/blob/develop/documentation/asciidoc/computers/compute-module/cm-emmc-flashing.adoc).
- [Raspberry Pi overlay parameters](https://github.com/raspberrypi/linux/blob/rpi-6.12.y/arch/arm/boot/dts/overlays/README)
  and [RP1 PWM/pinctrl bindings](https://github.com/raspberrypi/linux/blob/rpi-6.12.y/arch/arm64/boot/dts/broadcom/rp1.dtsi).
- [Orange Pi's official header GPIO mappings](https://github.com/orangepi-xunlong/wiringOP/blob/master/wiringPi/wiringPi.c),
  [vendor build sources](https://github.com/orangepi-xunlong/orangepi-build),
  [Armbian Orange Pi 5 USB host workaround](https://github.com/armbian/build/blob/main/packages/bsp/orangepi5/orangepi5-usb2-init.service).
  Actual signed-image device trees, pinmux groups and shipped overlay files were
  inspected for both Orange Pi boards.
- [Radxa Q6A specifications](https://github.com/radxa-docs/docs/blob/main/docs/dragon/q6a/README.md),
  [header pin table](https://github.com/radxa-docs/docs/blob/main/docs/dragon/q6a/hardware-use/pin-gpio.md),
  [exact Armbian kernel board source](https://github.com/radxa/kernel/blob/linux-6.18.2/arch/arm64/boot/dts/qcom/qcs6490-radxa-dragon-q6a.dts)
  and [GPIO PWM binding](https://github.com/radxa/kernel/blob/linux-6.18.2/Documentation/devicetree/bindings/pwm/pwm-gpio.yaml).
- [Radxa ROCK 5B documentation](https://docs.radxa.com/en/rock5/5b),
  [header pin table](https://docs.radxa.com/en/rock5/5b/hardware-design/hardware-interface),
  and the `rockchip-rk3588` board family in
  [Armbian's build framework](https://github.com/armbian/build/blob/main/config/boards/rock-5b.conf)
  (RK3588 device tree, `rk3588-spi0-m2-cs0-cs1-spidev`/`rk3588-uart2-m0`/`rk3588-pwm14-m0`
  overlays).
- [Radxa ROCK 5C header pin table](https://github.com/radxa-docs/docs/blob/main/docs/rock5/rock5c/hardware-design/hardware-interface.md),
  [Armbian board page](https://armbian.com/boards/rock-5c) and Armbian's vendor kernel
  [`rk3588s-rock-5c.dts`](https://github.com/armbian/linux-rockchip/blob/rk-6.1-rkr5.1/arch/arm64/boot/dts/rockchip/rk3588s-rock-5c.dts)
  and [overlays](https://github.com/armbian/linux-rockchip/tree/rk-6.1-rkr5.1/arch/arm64/boot/dts/rockchip/overlay)
  (`rk3588-spi4-m2-cs0-spidev`, `rk3588-uart2-m0`, `rk3588-pwm14-m1`).
- [Radxa Dragon Q8B documentation](https://docs.radxa.com/en/dragon/q8b),
  [header pin table](https://github.com/radxa-docs/docs/blob/main/docs/dragon/q8b/hardware-use/pin-gpio.md),
  the `sc8280xp` board family in
  [Armbian's build framework](https://github.com/armbian/build/blob/main/config/boards/radxa-dragon-q8b.conf)
  and the `radxa/kernel` `linux-7.0.11` branch
  ([`sc8280xp-radxa-dragon-q8b.dts`](https://github.com/radxa/kernel/blob/linux-7.0.11/arch/arm64/boot/dts/qcom/sc8280xp-radxa-dragon-q8b.dts),
  `qup_spi20_*`/`qup_uart17_*` pinctrl groups).
- [Radxa Cubie A7Z documentation](https://docs.radxa.com/en/cubie/a7z),
  [header pin table](https://github.com/radxa-docs/docs/blob/main/docs/cubie/a7z/hardware-use/pin-gpio.md),
  the [`cubie-a7z.csc` board definition](https://github.com/armbian/build/blob/main/config/boards/cubie-a7z.csc),
  the [`sun60iw2` family](https://github.com/armbian/build/blob/main/config/sources/families/sun60iw2.conf)
  and its [`0005-cubie-a7z-add-device-tree.patch`](https://github.com/armbian/build/blob/main/patch/kernel/archive/sun60iw2-opi-vendor/0005-cubie-a7z-add-device-tree.patch)
  (`sun60i-a733-uart2`/`sun60i-a733-pwm0` overlays, MBR partition table).
- [Linux userspace SPI driver binding](https://github.com/torvalds/linux/blob/master/Documentation/spi/spidev.rst),
  [systemd network configuration](https://www.freedesktop.org/software/systemd/man/latest/systemd.network.html),
  [Netplan configuration](https://netplan.readthedocs.io/en/stable/netplan-yaml/),
  and [python-periphery](https://github.com/vsergeev/python-periphery).
