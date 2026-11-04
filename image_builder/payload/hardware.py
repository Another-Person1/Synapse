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
        "overlays": ["rk3588-spi4-m0-cs1-spidev", "rk3588-uart0-m2", "rk3588-pwm1-m1"],
        "spi_pins": [19, 21, 23, 24],
        "uart_pins": [8, 10],
        "pwm_pins": [16],
    },
    "orangepi5-plus": {
        "dtb": "rockchip/rk3588-orangepi-5-plus.dtb",
        "overlays": ["rk3588-spi0-m2-cs0-spidev", "rk3588-uart6-m1", "rk3588-pwm14-m0"],
        "spi_pins": [19, 21, 23, 24],
        "uart_pins": [8, 10],
        "pwm_pins": [33],
    },
}
PI_DTBS = {
    "Pi 4": ["bcm2711-rpi-4-b.dtb"],
    "Pi 5": ["bcm2712-rpi-5-b.dtb", "bcm2712-d-rpi-5-b.dtb"],
    "CM4": ["bcm2711-rpi-cm4.dtb", "bcm2711-rpi-cm4-io.dtb"],
    "CM5": ["bcm2712-rpi-cm5-cm5io.dtb"],
}


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


def check_kernel(root, gadget=True, rp1=False):
    configs = list((root / "boot").glob("config-*"))
    if not configs:
        raise ValueError("No auditable kernel configuration in the image")
    required = ["CONFIG_GPIO_CDEV", "CONFIG_SPI_SPIDEV", "CONFIG_PWM", "CONFIG_SYSFS"]
    if gadget:
        required += ["CONFIG_USB_ETH", "CONFIG_USB_ETH_RNDIS"]
    if rp1:
        required += ["CONFIG_PWM_BCM2835", "CONFIG_PWM_RP1", "CONFIG_USB_DWC2"]
    for config in configs:
        content = config.read_text()
        # New kernels build PWM sysfs with CONFIG_SYSFS; old vendor kernels
        # still have the separate, optional CONFIG_PWM_SYSFS switch.
        if "# CONFIG_PWM_SYSFS is not set" in content:
            raise ValueError(f"PWM sysfs is disabled in {config.name}")
        if not gadget and not re.search(r"^CONFIG_PWM_GPIO=[ym]$", content, re.M):
            raise ValueError("Q6A requires the kernel GPIO PWM driver")
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
    # Armbian refreshes firmware overlays on kernel upgrades. Restore our
    # compiled overlay after the distro hook, without changing vendor trees.
    persisted = root / "usr/share/synapse-image/synapse-rp1-pwm.dtbo"
    persisted.parent.mkdir(parents=True, exist_ok=True)
    persisted.write_bytes((boot / "overlays/synapse-rp1-pwm.dtbo").read_bytes())
    write(
        root,
        "etc/kernel/postinst.d/zz-synapse-rp1-pwm",
        """#!/bin/sh
set -eu
install -m 0644 /usr/share/synapse-image/synapse-rp1-pwm.dtbo /boot/firmware/overlays/synapse-rp1-pwm.dtbo
""",
        0o755,
    )
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
    compatible(
        base,
        ["xunlong,orangepi-5", "rockchip,rk3588s-orangepi-5"]
        if board == "orangepi5"
        else ["xunlong,orangepi-5-plus", "rockchip,rk3588-orangepi-5-plus"],
    )
    destination = boot / "overlay-user/synapse-usb-c.dtbo"
    compile_overlay(HERE / "overlays/synapse-rockchip-usb-c.dts", destination)
    # Test the complete combination against the board tree, not each alone.
    with tempfile.TemporaryDirectory() as temporary:
        combined = Path(temporary) / "combined.dtb"
        merge_overlay(base, overlays + [destination], combined)
        symbols = (
            ("spi4", "uart0", "pwm1")
            if board == "orangepi5"
            else ("spi0", "uart6", "pwm14")
        )
        for symbol in (*symbols, "usbdrd_dwc3_0"):
            path = subprocess.check_output(
                ["fdtget", "-t", "s", str(combined), "/__symbols__", symbol], text=True
            ).strip()
            state = subprocess.check_output(
                ["fdtget", "-t", "s", str(combined), path, "status"], text=True
            ).strip()
            if state != "okay":
                raise ValueError(f"{board}: overlay did not enable {symbol}")
        role = subprocess.check_output(
            ["fdtget", "-t", "s", str(combined), path, "dr_mode"], text=True
        ).strip()
        if role != "peripheral":
            raise ValueError(f"{board}: USB-C is not configured for device mode")
    env = boot / "armbianEnv.txt"
    text = env.read_text()
    for key, additions in (
        ("overlays", profile["overlays"]),
        ("user_overlays", ["synapse-usb-c"]),
    ):
        old = re.search(rf"^{key}=(.*)$", text, re.M)
        existing = old.group(1).split() if old else []
        text = setting(text, key, " ".join(dict.fromkeys(existing + additions)))
    env.write_text(text)
    return {**profile, "pwm": "hardware", "usb_c_gadget": True}


def configure_q6a_dtb(dtb, overlay):
    compatible(dtb, "radxa,dragon-q6a")
    with tempfile.TemporaryDirectory(dir=dtb.parent) as temporary:
        merged = Path(temporary) / "merged.dtb"
        merge_overlay(dtb, [overlay], merged)
        # Generic SPI userspace binding is enabled by a narrowly scoped udev rule.
        path = "/soc@0/geniqup@ac0000/spi@a90000"
        status = subprocess.check_output(
            ["fdtget", "-t", "s", str(merged), path, "status"], text=True
        ).strip()
        if status != "okay":
            raise ValueError("Q6A SPI12 was not enabled")
        merged.chmod(dtb.stat().st_mode & 0o777)
        merged.replace(dtb)


def configure_q6a(root):
    overlay = root / "usr/share/synapse-image/synapse-q6a-spi.dtbo"
    compile_overlay(HERE / "overlays/synapse-q6a-spi.dts", overlay)
    trees = list((root / "boot").glob("armbian-dtb-*"))
    if not trees:
        raise ValueError("Q6A GRUB device tree is unavailable")
    for dtb in trees:
        configure_q6a_dtb(dtb, overlay)
    # Free the header UART for applications, including after kernel upgrades.
    write(
        root,
        "etc/default/grub.d/99-synapse-uart.cfg",
        """# Header UART belongs to applications, not a login console.
GRUB_CMDLINE_LINUX_DEFAULT=$(printf '%s' "${GRUB_CMDLINE_LINUX_DEFAULT:-}" | sed -E 's/(^| )(console=ttyMSM0[^ ]*|earlycon[^ ]*)//g')
GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT console=tty1"
GRUB_CMDLINE_LINUX=$(printf '%s' "${GRUB_CMDLINE_LINUX:-}" | sed -E 's/(^| )(console=ttyMSM0[^ ]*|earlycon[^ ]*)//g')
""",
    )
    write(
        root,
        "etc/kernel/postinst.d/zz-synapse-q6a-spi",
        """#!/bin/sh
set -eu
dtb=/boot/armbian-dtb-$1
[ ! -f "$dtb" ] || /usr/bin/python3 /usr/lib/synapse-image/hardware.py q6a-dtb --dtb "$dtb"
""",
        0o755,
    )
    return {
        "spi_pins": [19, 21, 23, 24],
        "uart_pins": [8, 10],
        "pwm_pins": [5],
        "pwm": "software (kernel pwm-gpio); no hardware PWM routed to the header",
        "usb_c_gadget": False,
        "usb_c_reason": "power-only; USB-A gadget operation excluded",
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
    check_kernel(root, gadget=board != "radxa-dragon-q6a", rp1=board == "rpi4b")
    if board == "rpi4b":
        report = configure_pi(root)
    elif board in ROCKCHIP:
        report = configure_rockchip(root, board)
    elif board == "radxa-dragon-q6a":
        report = configure_q6a(root)
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
    check_kernel(root, gadget=report["usb_c_gadget"], rp1=board == "rpi4b")
    if board == "rpi4b":
        boot = root / "boot/firmware"
        assert all(
            any((boot / name).is_file() for name in names) for names in PI_DTBS.values()
        )
        config = (boot / "config.txt").read_text()
        assert "dtparam=spi=on" in config and "dtoverlay=synapse-rp1-pwm" in config
        assert "console=serial0" not in (boot / "cmdline.txt").read_text()
    elif board == "radxa-dragon-q6a":
        for tree in (root / "boot").glob("armbian-dtb-*"):
            status = subprocess.check_output(
                [
                    "fdtget",
                    "-t",
                    "s",
                    str(tree),
                    "/soc@0/geniqup@ac0000/spi@a90000",
                    "status",
                ],
                text=True,
            ).strip()
            assert status == "okay"
    else:
        env = (root / "boot/armbianEnv.txt").read_text()
        assert all(name in env for name in ROCKCHIP[board]["overlays"])
        assert "synapse-usb-c" in env
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["configure", "q6a-dtb", "validate"])
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--board")
    parser.add_argument("--dtb", type=Path)
    args = parser.parse_args()
    if args.command == "validate":
        validate(args.root)
    elif args.command == "q6a-dtb":
        if args.dtb is None:
            parser.error("q6a-dtb requires --dtb")
        configure_q6a_dtb(
            args.dtb, Path("/usr/share/synapse-image/synapse-q6a-spi.dtbo")
        )
    else:
        if not args.board:
            parser.error("configure requires --board")
        configure(args.root, args.board)


if __name__ == "__main__":
    main()
