"""Configure researched appliance networking and board-specific header interfaces."""

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROCKCHIP = {
    "orangepi5": {
        "dtb": "rockchip/rk3588s-orangepi-5.dtb",
        "compatible": ["xunlong,orangepi-5", "rockchip,rk3588s-orangepi-5"],
        "overlays": ["rk3588-spi4-m0-cs1-spidev", "rk3588-uart0-m2", "rk3588-pwm1-m1"],
        "symbols": ("spi4", "uart0", "pwm1"),
        "spi_pins": [19, 21, 23, 24],
        "uart_pins": [8, 10],
        "pwm_pins": [16],
    },
    "orangepi5-plus": {
        "dtb": "rockchip/rk3588-orangepi-5-plus.dtb",
        "compatible": ["xunlong,orangepi-5-plus", "rockchip,rk3588-orangepi-5-plus"],
        "overlays": ["rk3588-spi0-m2-cs0-spidev", "rk3588-uart6-m1", "rk3588-pwm14-m0"],
        "symbols": ("spi0", "uart6", "pwm14"),
        "spi_pins": [19, 21, 23, 24],
        "uart_pins": [8, 10],
        "pwm_pins": [33],
    },
    "rock-5b": {
        "dtb": "rockchip/rk3588-rock-5b.dtb",
        # The SoC-wide "rockchip,rk3588" would accept any RK3588 board's tree.
        "compatible": ["radxa,rock-5b"],
        "overlays": [
            "rk3588-spi0-m2-cs0-cs1-spidev",
            "rk3588-uart2-m0",
            "rk3588-pwm14-m0",
        ],
        "symbols": ("spi0", "uart2", "pwm14"),
        # rk3588-uart2-m0 disables the vendor fiq_debugger (ttyFIQ0) and turns
        # header pins 8/10 into a plain /dev/ttyS2 for applications; the kernel
        # console stays on the display and no login shell uses the header UART.
        "armbian_env": {"console": "display"},
        "spi_pins": [19, 21, 23, 24, 26],
        "uart_pins": [8, 10],
        "pwm_pins": [32],
    },
    "rock-5c": {
        "dtb": "rockchip/rk3588s-rock-5c.dtb",
        "compatible": ["radxa,rock-5c"],
        # Pins 19/21/23/24 = SPI4 M2 MOSI/MISO/CLK/CS0, pins 8/10 = UART2 M0
        # (same fiq_debugger hand-off as the ROCK 5B), pin 13 = PWM14 M1.
        "overlays": [
            "rk3588-spi4-m2-cs0-spidev",
            "rk3588-uart2-m0",
            "rk3588-pwm14-m1",
        ],
        "symbols": ("spi4", "uart2", "pwm14"),
        "armbian_env": {"console": "display"},
        # USB-C is power-only; the OTG controller sits behind a USB-A port,
        # which is excluded from gadget use like on the Dragon Q6A.
        "usb_c_gadget": False,
        "usb_c_reason": "USB-C is power-only; USB-A OTG gadget operation excluded",
        "spi_pins": [19, 21, 23, 24],
        "uart_pins": [8, 10],
        "pwm_pins": [13],
    },
}
# Qualcomm boards boot through GRUB with a per-kernel device tree file; a
# compiled overlay is merged into every /boot/armbian-dtb-* tree.
QCOM = {
    "radxa-dragon-q6a": {
        "compatible": "radxa,dragon-q6a",
        "spi_path": "/soc@0/geniqup@ac0000/spi@a90000",
        "overlay": "synapse-q6a-spi.dts",
        "console": "ttyMSM0",
        "spi_pins": [19, 21, 23, 24],
        "uart_pins": [8, 10],
        "pwm_pins": [5],
    },
    "radxa-dragon-q8b": {
        "compatible": "radxa,dragon-q8b",
        # SPI20 (GPIO87-90) on header pins 19/21/23/24, CS0 only.
        "spi_path": "/soc@0/geniqup@8c0000/spi@890000",
        "overlay": "synapse-q8b-spi.dts",
        "console": "ttyMSM0",
        "spi_pins": [19, 21, 23, 24],
        "uart_pins": [8, 10],
        "pwm_pins": [5],
    },
}
# Armbian's own header, written by its bsp-cli postinst when the file is absent.
QCOM_ENV_HEADER = (
    "# This board boots with GRUB. Only overlay_prefix, overlays and "
    "user_overlays are read from this file.\n"
)
# Allwinner boards apply overlays listed in armbianEnv.txt at boot; SPI1 is
# already enabled with spidev in the base tree, so only UART/PWM need overlays.
ALLWINNER = {
    "cubie-a7z": {
        "dtb": "allwinner/sun60i-a733-cubie-a7z.dtb",
        # The vendor tree carries only SoC compatibles, so pin the board model.
        "compatible": ["allwinner,a733", "arm,sun60iw2p1"],
        "model": "Radxa Cubie A7Z",
        "overlay_dir": "dtb/allwinner/overlay",
        "overlays": ["sun60i-a733-uart2", "sun60i-a733-pwm0"],
        "symbols": ("spi1", "uart2", "pwm0_0"),
        "spi_pins": [19, 21, 23, 24],
        "uart_pins": [7, 11],
        "pwm_pins": [36],
    },
}
PI_DTBS = {
    "Pi 4": ["bcm2711-rpi-4-b.dtb"],
    "Pi 5": ["bcm2712-rpi-5-b.dtb", "bcm2712-d-rpi-5-b.dtb"],
    "CM4": ["bcm2711-rpi-cm4.dtb", "bcm2711-rpi-cm4-io.dtb"],
    "CM5": ["bcm2712-rpi-cm5-cm5io.dtb"],
}
# run-parts runs hooks in C-locale name order. The name must sort after
# Armbian's zzz-copy-new-files / zzz-remove-old-files ("zzz-" < "zzzz"), which
# recreate the overlays directory; an earlier hook's copy would be deleted.
PI_OVERLAY_HOOK = "zzzz-synapse-rp1-pwm"
PI_OVERLAY_HOOK_DIRECTORIES = ("etc/kernel/postinst.d", "etc/kernel/postrm.d")
PI_OVERLAY_RESTORE = """#!/bin/sh
set -eu
# Removing the last kernel also removes the overlays directory; nothing to restore.
[ -d /boot/firmware/overlays ] || exit 0
install -m 0644 /usr/share/synapse-image/synapse-rp1-pwm.dtbo /boot/firmware/overlays/synapse-rp1-pwm.dtbo
"""


def write(root, name, content, mode=0o644):
    path = root / name.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(mode)


def setting(text, name, value):
    """Replace an env-file assignment without disturbing unrelated settings."""
    lines = [
        line
        for line in text.splitlines()
        if not re.match(rf"^{re.escape(name)}=", line)
    ]
    return "\n".join(lines + [f"{name}={value}"]) + "\n"


def compile_overlay(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["dtc", "-@", "-I", "dts", "-O", "dtb", "-o", str(destination), str(source)],
        check=True,
    )


def merge_overlay(base, overlays, destination):
    subprocess.run(
        [
            "fdtoverlay",
            "-i",
            str(base),
            "-o",
            str(destination),
            *(str(p) for p in overlays),
        ],
        check=True,
    )


def compatible(dtb, expected):
    actual = subprocess.check_output(
        ["fdtget", "-t", "s", str(dtb), "/", "compatible"], text=True
    ).split()
    expected = [expected] if isinstance(expected, str) else expected
    if not any(item in actual for item in expected):
        raise ValueError(f"Unexpected device tree for {expected}: {dtb}")


def check_kernel(root, gadget=True, rp1=False, require_pwm_gpio=False):
    configs = list((root / "boot").glob("config-*"))
    if not configs:
        raise ValueError("No auditable kernel configuration in the image")
    required = ["CONFIG_GPIO_CDEV", "CONFIG_SPI_SPIDEV", "CONFIG_PWM", "CONFIG_SYSFS"]
    if gadget:
        # synapse-usb-ethernet builds a configfs RNDIS + CDC-ECM gadget.
        required += [
            "CONFIG_USB_CONFIGFS",
            "CONFIG_USB_CONFIGFS_RNDIS",
            "CONFIG_USB_CONFIGFS_ECM",
        ]
    if rp1:
        required += ["CONFIG_PWM_BCM2835", "CONFIG_PWM_RP1", "CONFIG_USB_DWC2"]
    for config in configs:
        content = config.read_text()
        # New kernels build PWM sysfs with CONFIG_SYSFS; old vendor kernels
        # still have the separate, optional CONFIG_PWM_SYSFS switch.
        if "# CONFIG_PWM_SYSFS is not set" in content:
            raise ValueError(f"PWM sysfs is disabled in {config.name}")
        if require_pwm_gpio and not re.search(r"^CONFIG_PWM_GPIO=[ym]$", content, re.M):
            raise ValueError(
                "This board routes no hardware PWM to the header and requires "
                "the kernel GPIO PWM driver"
            )
        for option in required:
            if not re.search(rf"^{option}=[ym]$", content, re.M):
                raise ValueError(f"Required {option} is unavailable in {config.name}")


def configure_pi(root):
    boot = root / "boot/firmware"
    if not (boot / "config.txt").is_file():
        raise ValueError("Raspberry Pi firmware partition is not mounted")
    for model, filenames in PI_DTBS.items():
        if not any((boot / name).is_file() for name in filenames):
            raise ValueError(f"Shared image is missing a {model} device tree")
    for name in ("dwc2", "disable-bt", "uart0-pi5", "pwm-2chan"):
        if not (boot / "overlays" / f"{name}.dtbo").is_file():
            raise ValueError(f"Missing Raspberry Pi firmware overlay: {name}")
    compile_overlay(
        HERE / "overlays/synapse-rp1-pwm.dts", boot / "overlays/synapse-rp1-pwm.dtbo"
    )
    # Armbian replaces /boot/firmware/overlays wholesale when a kernel is
    # installed (postinst.d/zzz-copy-new-files) or removed
    # (postrm.d/zzz-remove-old-files). Restore our compiled overlay after those
    # hooks, without changing vendor trees.
    persisted = root / "usr/share/synapse-image/synapse-rp1-pwm.dtbo"
    persisted.parent.mkdir(parents=True, exist_ok=True)
    persisted.write_bytes((boot / "overlays/synapse-rp1-pwm.dtbo").read_bytes())
    for directory in PI_OVERLAY_HOOK_DIRECTORIES:
        write(root, f"{directory}/{PI_OVERLAY_HOOK}", PI_OVERLAY_RESTORE, 0o755)
    # Resolve the new RP1 overlay against both actual signed-base trees.
    with tempfile.TemporaryDirectory() as temporary:
        for model in ("Pi 5", "CM5"):
            for name in PI_DTBS[model]:
                if (boot / name).is_file():
                    merge_overlay(
                        boot / name,
                        [boot / "overlays/synapse-rp1-pwm.dtbo"],
                        Path(temporary) / name,
                    )
    config = boot / "config.txt"
    text = re.sub(
        r"\n# BEGIN SYNAPSE\n.*?# END SYNAPSE\n?", "\n", config.read_text(), flags=re.S
    )
    # Supersede Armbian's host defaults for carriers routing USB2 to USB-C.
    # Carrier-specific routing and power still require physical validation.
    text = re.sub(
        r"^dtoverlay=dwc2,dr_mode=host\s*$",
        "# Synapse reserves CM5 USB-C for device networking",
        text,
        flags=re.M,
    )
    text += """
# BEGIN SYNAPSE
[all]
dtparam=spi=on
enable_uart=1
# Analogue audio shares the PWM controller on BCM2711.
dtparam=audio=off
[pi4]
dtoverlay=disable-bt
dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
[board-type=0x11]
# Pi 4B and CM4 have distinct USB2 host-mode firmware defaults.
dtoverlay=dwc2,dr_mode=peripheral
[cm4]
otg_mode=0
dtoverlay=dwc2,dr_mode=peripheral
[pi5]
dtoverlay=uart0-pi5
dtoverlay=synapse-rp1-pwm
dtoverlay=dwc2,dr_mode=peripheral
[all]
# END SYNAPSE
"""
    config.write_text(text)
    cmdline = boot / "cmdline.txt"
    words = cmdline.read_text().split()
    cmdline.write_text(
        " ".join(
            word
            for word in words
            if not re.match(r"console=(serial0|ttyAMA0|ttyS0)(,|$)", word)
        )
        + "\n"
    )
    return {
        "models": list(PI_DTBS),
        "spi_pins": [19, 21, 23, 24, 26],
        "uart_pins": [8, 10],
        "pwm_pins": [32, 33],
        "pwm": "hardware",
        "usb_c_gadget": True,
        "cm4_gadget": "USB-C carrier assumed; confirm routing and power with the carrier vendor",
    }


def configure_rockchip(root, board):
    profile = ROCKCHIP[board]
    boot = root / "boot"
    # Armbian's dtb symlink must resolve inside this image, not on the host.
    directory = boot / "dtb/rockchip/overlay"
    overlays = [directory / f"{name}.dtbo" for name in profile["overlays"]]
    base = boot / "dtb" / profile["dtb"]
    if not base.is_file() or not all(p.is_file() for p in overlays):
        raise ValueError(f"{board}: expected official header overlays are unavailable")
    compatible(base, profile["compatible"])
    gadget = profile.get("usb_c_gadget", True)
    merged = list(overlays)
    if gadget:
        destination = boot / "overlay-user/synapse-usb-c.dtbo"
        compile_overlay(HERE / "overlays/synapse-rockchip-usb-c.dts", destination)
        merged.append(destination)
    # Test the complete combination against the board tree, not each alone.
    with tempfile.TemporaryDirectory() as temporary:
        combined = Path(temporary) / "combined.dtb"
        merge_overlay(base, merged, combined)
        for symbol in (*profile["symbols"], *(["usbdrd_dwc3_0"] if gadget else [])):
            path = subprocess.check_output(
                ["fdtget", "-t", "s", str(combined), "/__symbols__", symbol], text=True
            ).strip()
            state = subprocess.check_output(
                ["fdtget", "-t", "s", str(combined), path, "status"], text=True
            ).strip()
            if state != "okay":
                raise ValueError(f"{board}: overlay did not enable {symbol}")
        if gadget:
            role = subprocess.check_output(
                ["fdtget", "-t", "s", str(combined), path, "dr_mode"], text=True
            ).strip()
            if role != "peripheral":
                raise ValueError(f"{board}: USB-C is not configured for device mode")
    env = boot / "armbianEnv.txt"
    text = env.read_text()
    for key, additions in (
        ("overlays", profile["overlays"]),
        ("user_overlays", ["synapse-usb-c"] if gadget else []),
    ):
        if not additions:
            continue
        old = re.search(rf"^{key}=(.*)$", text, re.M)
        existing = old.group(1).split() if old else []
        text = setting(text, key, " ".join(dict.fromkeys(existing + additions)))
    for key, value in profile.get("armbian_env", {}).items():
        text = setting(text, key, value)
    env.write_text(text)
    return {**profile, "pwm": "hardware", "usb_c_gadget": gadget}


def overlay_name(board):
    return QCOM[board]["overlay"].removesuffix(".dts") + ".dtbo"


def configure_qcom_dtb(dtb, overlay, board):
    profile = QCOM[board]
    compatible(dtb, profile["compatible"])
    with tempfile.TemporaryDirectory(dir=dtb.parent) as temporary:
        merged = Path(temporary) / "merged.dtb"
        merge_overlay(dtb, [overlay], merged)
        # Generic SPI userspace binding is enabled by a narrowly scoped udev rule.
        status = subprocess.check_output(
            ["fdtget", "-t", "s", str(merged), profile["spi_path"], "status"],
            text=True,
        ).strip()
        if status != "okay":
            raise ValueError(f"{board}: header SPI was not enabled at {profile['spi_path']}")
        merged.chmod(dtb.stat().st_mode & 0o777)
        merged.replace(dtb)


def register_user_overlay(env, name):
    """List a /boot/overlay-user overlay in armbianEnv.txt, keeping existing ones."""
    text = env.read_text() if env.is_file() else QCOM_ENV_HEADER
    old = re.search(r"^user_overlays=(.*)$", text, re.M)
    existing = old.group(1).split() if old else []
    env.write_text(setting(text, "user_overlays", " ".join(dict.fromkeys(existing + [name]))))


def configure_qcom(root, board):
    profile = QCOM[board]
    overlay = root / "usr/share/synapse-image" / overlay_name(board)
    compile_overlay(HERE / "overlays" / profile["overlay"], overlay)
    trees = list((root / "boot").glob("armbian-dtb-*"))
    if not trees:
        raise ValueError(f"{board} GRUB device tree is unavailable")
    for dtb in trees:
        configure_qcom_dtb(dtb, overlay, board)
    # Armbian's grub-with-dtb hook, and its armbian-grub-dtb-overlays watcher
    # (run whenever armbianEnv.txt changes, e.g. via armbian-config or a
    # bsp-cli upgrade), rebuild every /boot/armbian-dtb-* from the pristine
    # kernel DTB plus only the overlays listed in armbianEnv.txt. Register ours
    # as a user overlay so those rebuilds keep the header SPI enabled.
    user = root / "boot/overlay-user" / overlay_name(board)
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_bytes(overlay.read_bytes())
    user.chmod(0o644)
    register_user_overlay(root / "boot/armbianEnv.txt", user.stem)
    console = profile["console"]
    # Free the header UART for applications, including after kernel upgrades.
    write(
        root,
        "etc/default/grub.d/99-synapse-uart.cfg",
        f"""# Header UART belongs to applications, not a login console.
GRUB_CMDLINE_LINUX_DEFAULT=$(printf '%s' "${{GRUB_CMDLINE_LINUX_DEFAULT:-}}" | sed -E 's/(^| )(console={console}[^ ]*|earlycon[^ ]*)//g')
GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT console=tty1"
GRUB_CMDLINE_LINUX=$(printf '%s' "${{GRUB_CMDLINE_LINUX:-}}" | sed -E 's/(^| )(console={console}[^ ]*|earlycon[^ ]*)//g')
""",
    )
    subcommand = board.removeprefix("radxa-dragon-") + "-dtb"
    write(
        root,
        f"etc/kernel/postinst.d/zz-synapse-{board.removeprefix('radxa-dragon-')}-spi",
        f"""#!/bin/sh
set -eu
dtb=/boot/armbian-dtb-$1
[ ! -f "$dtb" ] || /usr/bin/python3 /usr/lib/synapse-image/hardware.py {subcommand} --dtb "$dtb"
""",
        0o755,
    )
    return {
        "spi_pins": profile["spi_pins"],
        "uart_pins": profile["uart_pins"],
        "pwm_pins": profile["pwm_pins"],
        "pwm": "software (kernel pwm-gpio); no hardware PWM routed to the header",
        "usb_c_gadget": False,
        "usb_c_reason": "USB controllers are host-only on this board; gadget networking is unavailable",
    }


def configure_allwinner(root, board):
    profile = ALLWINNER[board]
    boot = root / "boot"
    # Armbian's dtb symlink must resolve inside this image, not on the host.
    directory = boot / profile["overlay_dir"]
    overlays = [directory / f"{name}.dtbo" for name in profile["overlays"]]
    base = boot / "dtb" / profile["dtb"]
    if not base.is_file() or not all(p.is_file() for p in overlays):
        raise ValueError(f"{board}: expected official header overlays are unavailable")
    compatible(base, profile["compatible"])
    model = subprocess.check_output(
        ["fdtget", "-t", "s", str(base), "/", "model"], text=True
    ).strip()
    if model != profile["model"]:
        raise ValueError(f"Unexpected device tree model for {board}: {model}")
    # Test the complete combination against the board tree, not each alone.
    with tempfile.TemporaryDirectory() as temporary:
        combined = Path(temporary) / "combined.dtb"
        merge_overlay(base, overlays, combined)
        for symbol in profile["symbols"]:
            path = subprocess.check_output(
                ["fdtget", "-t", "s", str(combined), "/__symbols__", symbol], text=True
            ).strip()
            state = subprocess.check_output(
                ["fdtget", "-t", "s", str(combined), path, "status"], text=True
            ).strip()
            if state != "okay":
                raise ValueError(f"{board}: overlay did not enable {symbol}")
    env = boot / "armbianEnv.txt"
    text = env.read_text()
    old = re.search(r"^overlays=(.*)$", text, re.M)
    existing = old.group(1).split() if old else []
    text = setting(text, "overlays", " ".join(dict.fromkeys(existing + profile["overlays"])))
    env.write_text(text)
    return {
        **profile,
        "pwm": "hardware",
        "usb_c_gadget": False,
        "usb_c_reason": "usbc0 is host-only (OTG broken); the board networks over WiFi 6",
    }


def configure_common(root):
    write(root, "etc/hostname", "synapse\n")
    hosts = root / "etc/hosts"
    content = re.sub(
        r"^127\.0\.1\.1\s+.*$", "127.0.1.1\tsynapse", hosts.read_text(), flags=re.M
    )
    if not re.search(r"^127\.0\.1\.1\s", content, re.M):
        content += "\n127.0.1.1\tsynapse\n"
    hosts.write_text(content)
    write(
        root,
        "etc/avahi/services/synapse.service",
        """<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group><name replace-wildcards="yes">Synapse on %h</name>
<service><type>_http._tcp</type><port>3000</port></service>
<service><type>_synapse._tcp</type><port>3000</port></service>
</service-group>
""",
    )
    for source in (HERE / "network").iterdir():
        target = "etc/netplan" if source.suffix == ".yaml" else "etc/systemd/network"
        write(
            root,
            f"{target}/{source.name}",
            source.read_text(),
            0o600 if source.suffix == ".yaml" else 0o644,
        )
    write(root, "etc/modules-load.d/synapse-io.conf", "spidev\n")


def configure(root, board):
    if board == "rpi4b":
        check_kernel(root, gadget=True, rp1=True)
        report = configure_pi(root)
    elif board in ROCKCHIP:
        check_kernel(root, gadget=ROCKCHIP[board].get("usb_c_gadget", True))
        report = configure_rockchip(root, board)
    elif board in QCOM:
        check_kernel(root, gadget=False, require_pwm_gpio=True)
        report = configure_qcom(root, board)
    elif board in ALLWINNER:
        check_kernel(root, gadget=False)
        report = configure_allwinner(root, board)
    else:
        raise ValueError(f"Unsupported hardware profile: {board}")
    configure_common(root)
    write(root, "etc/synapse-image/armbian-board", board + "\n")
    if report["usb_c_gadget"]:
        write(root, "etc/synapse-image/usb-gadget-enabled", "USB-C data port only\n")
    report.update(
        {
            "armbian_board": board,
            "hostname": "synapse",
            "dashboard": "http://synapse.local:3000",
            "header_i2c_enabled_by_synapse": False,
        }
    )
    write(
        root,
        "usr/share/synapse-image/hardware.json",
        json.dumps(report, indent=2) + "\n",
    )


def validate(root):
    import xml.etree.ElementTree as xml

    report = json.loads((root / "usr/share/synapse-image/hardware.json").read_text())
    board = report["armbian_board"]
    if (root / "etc/hostname").read_text().strip() != "synapse":
        raise ValueError("Default appliance hostname is missing")
    services = xml.parse(root / "etc/avahi/services/synapse.service")
    assert services.find(".//service/port").text == "3000"
    assert (root / "etc/synapse-image/usb-gadget-enabled").exists() == report[
        "usb_c_gadget"
    ]
    check_kernel(
        root,
        gadget=report["usb_c_gadget"],
        rp1=board == "rpi4b",
        require_pwm_gpio=board in QCOM,
    )
    if board == "rpi4b":
        boot = root / "boot/firmware"
        assert all(
            any((boot / name).is_file() for name in names) for names in PI_DTBS.values()
        )
        config = (boot / "config.txt").read_text()
        assert "dtparam=spi=on" in config and "dtoverlay=synapse-rp1-pwm" in config
        assert "console=serial0" not in (boot / "cmdline.txt").read_text()
        for directory in PI_OVERLAY_HOOK_DIRECTORIES:
            hooks = sorted(path.name for path in (root / directory).iterdir())
            # Our restore must be the last hook, after any overlay refresh.
            assert hooks[-1] == PI_OVERLAY_HOOK, f"{directory} order: {hooks}"
    elif board in QCOM:
        for tree in (root / "boot").glob("armbian-dtb-*"):
            status = subprocess.check_output(
                [
                    "fdtget",
                    "-t",
                    "s",
                    str(tree),
                    QCOM[board]["spi_path"],
                    "status",
                ],
                text=True,
            ).strip()
            assert status == "okay"
        name = overlay_name(board)
        user = root / "boot/overlay-user" / name
        assert user.read_bytes() == (root / "usr/share/synapse-image" / name).read_bytes()
        env = (root / "boot/armbianEnv.txt").read_text()
        listed = re.search(r"^user_overlays=(.*)$", env, re.M)
        assert listed and user.stem in listed.group(1).split()
    elif board in ROCKCHIP:
        env = (root / "boot/armbianEnv.txt").read_text()
        assert all(name in env for name in ROCKCHIP[board]["overlays"])
        assert ("synapse-usb-c" in env) == ROCKCHIP[board].get("usb_c_gadget", True)
        for key, value in ROCKCHIP[board].get("armbian_env", {}).items():
            assert re.search(rf"^{key}={re.escape(value)}$", env, re.M)
    elif board in ALLWINNER:
        env = (root / "boot/armbianEnv.txt").read_text()
        assert all(name in env for name in ALLWINNER[board]["overlays"])
    print(json.dumps(report, indent=2))


def main():
    dtb_commands = [f"{board.removeprefix('radxa-dragon-')}-dtb" for board in QCOM]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["configure", "validate", *dtb_commands])
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--board")
    parser.add_argument("--dtb", type=Path)
    args = parser.parse_args()
    if args.command == "validate":
        validate(args.root)
    elif args.command.endswith("-dtb"):
        board = "radxa-dragon-" + args.command.removesuffix("-dtb")
        if args.dtb is None:
            parser.error(f"{args.command} requires --dtb")
        configure_qcom_dtb(
            args.dtb,
            Path("/usr/share/synapse-image") / overlay_name(board),
            board,
        )
    else:
        if not args.board:
            parser.error("configure requires --board")
        configure(args.root, args.board)


if __name__ == "__main__":
    main()
