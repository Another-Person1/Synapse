import os
import shutil
import subprocess
import xml.etree.ElementTree as xml

import pytest

from image_builder import build
from image_builder.payload import hardware

HAS_DTC = shutil.which("dtc") and shutil.which("fdtoverlay")


@pytest.mark.parametrize(
    "alias", ["raspberrypi", "rpi4b", "raspberrypi5", "cm4", "cm5"]
)
def test_pi_aliases_produce_one_generically_named_artifact(alias):
    assert build.image_targets(alias) == ["raspberrypi"]
    assert build.BOARDS[alias]["armbian_board"] == "rpi4b"
    assert build.image_targets().count("raspberrypi") == 1
    expected_targets = {v.get("image_target", k) for k, v in build.BOARDS.items()}
    assert len(build.image_targets()) == len(expected_targets)


def test_every_board_has_a_hardware_profile():
    for board in build.BOARDS:
        armbian_board = build.BOARDS[board]["armbian_board"]
        assert (
            armbian_board == "rpi4b"
            or armbian_board in hardware.ROCKCHIP
            or armbian_board in hardware.QCOM
            or armbian_board in hardware.ALLWINNER
        ), board


def test_hostname_and_discovery_preserve_loopback_and_ipv6(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/hosts").write_text(
        "127.0.0.1 localhost\n127.0.1.1 rpi4b\n::1 localhost ip6-localhost\n"
    )
    hardware.configure_common(tmp_path)
    hardware.configure_common(tmp_path)
    hosts = (tmp_path / "etc/hosts").read_text()
    assert hosts.count("127.0.1.1") == 1
    assert "127.0.0.1 localhost" in hosts and "::1 localhost ip6-localhost" in hosts
    assert (tmp_path / "etc/hostname").read_text() == "synapse\n"
    service = xml.parse(tmp_path / "etc/avahi/services/synapse.service")
    assert {n.text for n in service.findall(".//type")} == {
        "_http._tcp",
        "_synapse._tcp",
    }
    assert {n.text for n in service.findall(".//port")} == {"3000"}


def test_env_edits_preserve_boot_identity():
    before = "rootdev=UUID=keep\nfdtfile=rockchip/board.dtb\noverlays=old\n"
    after = hardware.setting(before, "overlays", "old new")
    assert "rootdev=UUID=keep\nfdtfile=rockchip/board.dtb\n" in after
    assert after.count("overlays=") == 1


def test_missing_cm5_tree_fails_instead_of_claiming_shared_compatibility(tmp_path):
    boot = tmp_path / "boot/firmware"
    boot.mkdir(parents=True)
    (boot / "config.txt").write_text("[all]\n")
    for names in list(hardware.PI_DTBS.values())[:-1]:
        (boot / names[0]).touch()
    with pytest.raises(ValueError, match="CM5"):
        hardware.configure_pi(tmp_path)


def test_new_kernel_without_old_pwm_sysfs_option_is_supported(tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    config = boot / "config-6.18"
    config.write_text(
        "\n".join(
            f"CONFIG_{name}=y"
            for name in ("GPIO_CDEV", "SPI_SPIDEV", "PWM", "SYSFS", "PWM_GPIO")
        )
    )
    hardware.check_kernel(tmp_path, gadget=False)
    config.write_text(config.read_text() + "\n# CONFIG_PWM_SYSFS is not set\n")
    with pytest.raises(ValueError, match="PWM sysfs"):
        hardware.check_kernel(tmp_path, gadget=False)


@pytest.mark.skipif(not HAS_DTC, reason="Device tree compiler required")
def test_q6a_overlay_is_valid_repeatable_and_keeps_i2c_disabled(tmp_path):
    source = tmp_path / "base.dts"
    source.write_text("""/dts-v1/;
/ { compatible = "radxa,dragon-q6a", "qcom,qcm6490";
    tlmm: gpio { gpio-controller; #gpio-cells = <2>; };
    soc@0 { geniqup@ac0000 {
        spi12: spi@a90000 { #address-cells = <1>; #size-cells = <0>; status = "disabled"; };
        i2c@a90000 { status = "disabled"; };
    }; };
};""")
    base, overlay = tmp_path / "base.dtb", tmp_path / "overlay.dtbo"
    hardware.compile_overlay(source, base)
    hardware.compile_overlay(hardware.HERE / "overlays/synapse-q6a-spi.dts", overlay)
    for _ in range(2):
        hardware.configure_qcom_dtb(base, overlay, "radxa-dragon-q6a")

        def value(path, name):
            return subprocess.check_output(
                ["fdtget", "-t", "s", str(base), path, name], text=True
            ).strip()

        assert value("/soc@0/geniqup@ac0000/spi@a90000", "status") == "okay"
        assert value("/soc@0/geniqup@ac0000/i2c@a90000", "status") == "disabled"
        assert value("/synapse-pwm", "compatible") == "pwm-gpio"
    wrong = tmp_path / "wrong.dts"
    wrong.write_text('/dts-v1/; / { compatible = "wrong,board"; };')
    hardware.compile_overlay(wrong, base)
    with pytest.raises(ValueError, match="Unexpected device tree"):
        hardware.configure_qcom_dtb(base, overlay, "radxa-dragon-q6a")


@pytest.mark.skipif(not HAS_DTC, reason="Device tree compiler required")
def test_q8b_overlay_enables_spi20_with_pinctrl_and_pwm_gpio(tmp_path):
    source = tmp_path / "base.dts"
    source.write_text("""/dts-v1/;
/ { compatible = "radxa,dragon-q8b", "qcom,sc8280xp";
    tlmm: gpio { gpio-controller; #gpio-cells = <2>; };
    soc@0 { geniqup@8c0000 {
        qup_spi20_data_clk: pinctrl-data { };
        qup_spi20_cs: pinctrl-cs { };
        spi20: spi@890000 { #address-cells = <1>; #size-cells = <0>; status = "disabled"; };
    }; };
};""")
    base, overlay = tmp_path / "base.dtb", tmp_path / "overlay.dtbo"
    hardware.compile_overlay(source, base)
    hardware.compile_overlay(hardware.HERE / "overlays/synapse-q8b-spi.dts", overlay)
    hardware.configure_qcom_dtb(base, overlay, "radxa-dragon-q8b")

    def value(path, name):
        return subprocess.check_output(
            ["fdtget", "-t", "s", str(base), path, name], text=True
        ).strip()

    spi = "/soc@0/geniqup@8c0000/spi@890000"
    assert value(spi, "status") == "okay"
    assert value(spi, "pinctrl-names") == "default"
    assert value(f"{spi}/userspace-spi@0", "compatible") == "synapse,userspace-spi"
    assert value("/synapse-pwm", "compatible") == "pwm-gpio"
    wrong = tmp_path / "wrong.dts"
    wrong.write_text('/dts-v1/; / { compatible = "radxa,dragon-q6a"; };')
    hardware.compile_overlay(wrong, base)
    with pytest.raises(ValueError, match="Unexpected device tree"):
        hardware.configure_qcom_dtb(base, overlay, "radxa-dragon-q8b")


VENDOR_ROCKCHIP_USB_C = """/dts-v1/;
/ { compatible = "xunlong,orangepi-5", "rockchip,rk3588s";
    usbdrd3_0 { usbdrd_dwc3_0: usb@fc000000 {
        dr_mode = "otg"; usb-role-switch; status = "okay"; }; };
    i2c@fec80000 { fusb302@22 { compatible = "fcs,fusb302";
        usb_con: connector { compatible = "usb-c-connector";
            data-role = "dual"; power-role = "dual"; try-power-role = "sink"; }; }; };
};"""


@pytest.mark.skipif(not HAS_DTC, reason="Device tree compiler required")
def test_rockchip_usb_c_is_forced_to_device_through_type_c(tmp_path):
    # Shape of Armbian's vendor rk3588s-orangepi-5 / rk3588-orangepi-5-plus /
    # rk3588-rock-5b trees: dual-role FUSB302 connector, DWC3 on a role switch.
    source = tmp_path / "base.dts"
    source.write_text(VENDOR_ROCKCHIP_USB_C)
    base, overlay = tmp_path / "base.dtb", tmp_path / "usb-c.dtbo"
    combined = tmp_path / "combined.dtb"
    hardware.compile_overlay(source, base)
    with pytest.raises(ValueError, match="power-role"):
        hardware.check_rockchip_usb_c(base, "orangepi5")
    hardware.compile_overlay(hardware.HERE / "overlays/synapse-rockchip-usb-c.dts", overlay)
    hardware.merge_overlay(base, [overlay], combined)
    hardware.check_rockchip_usb_c(combined, "orangepi5")
    connector = hardware.symbol_path(combined, "usb_con")
    assert hardware.dt_string(combined, connector, "try-power-role") == "sink"
    # The old approach (fixed peripheral mode) must be rejected.
    source.write_text(VENDOR_ROCKCHIP_USB_C.replace('dr_mode = "otg"', 'dr_mode = "peripheral"'))
    hardware.compile_overlay(source, base)
    peripheral = tmp_path / "peripheral.dtb"
    hardware.merge_overlay(base, [overlay], peripheral)
    hardware.check_rockchip_usb_c(peripheral, "orangepi5")  # overlay restores otg


def test_rockchip_usb_c_overlay_keeps_the_role_switch():
    text = (hardware.HERE / "overlays/synapse-rockchip-usb-c.dts").read_text()
    assert 'dr_mode = "peripheral"' not in text
    for needle in ('power-role = "sink"', 'data-role = "device"', 'dr_mode = "otg"'):
        assert needle in text


def test_ethernet_catch_alls_yield_to_per_interface_definitions():
    import re

    text = (hardware.HERE / "network/90-synapse-ethernet.yaml").read_text()
    # Definition ids are the keys indented four spaces under "ethernets:".
    ids = re.findall(r"^    ([A-Za-z0-9_.-]+):\s*$", text, re.M)
    assert ids == ["zz-synapse-ethernet", "zz-synapse-usb-adapters"]
    # networkd takes the first matching 10-netplan-<id>.network by name; a
    # static address the UI sets on eth0/end1/enP4p65s0 must sort first.
    for interface in ("eth0", "end1", "enP4p65s0", "enx001122334455", "usb0"):
        assert all(f"10-netplan-{interface}.network" < f"10-netplan-{i}.network" for i in ids)
    assert text.count("link-local: [ipv4, ipv6]") == 2
    assert text.count("optional: true") == 2
    assert hardware.ETHERNET_NETWORK == f"10-netplan-{ids[0]}.network"


def test_direct_link_dropin_targets_the_ethernet_catch_all(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/hosts").write_text("127.0.0.1 localhost\n")
    hardware.configure_common(tmp_path)
    dropin = (
        tmp_path
        / "etc/systemd/network"
        / f"{hardware.ETHERNET_NETWORK}.d/50-synapse-direct-link.conf"
    )
    assert "IPv4LLStartAddress=169.254.194.1" in dropin.read_text()
    # A drop-in directory beside it must not be mistaken for a network file.
    assert not (tmp_path / "etc/systemd/network" / hardware.ETHERNET_NETWORK).exists()


def test_qcom_user_overlay_registration_keeps_existing_entries(tmp_path):
    env = tmp_path / "armbianEnv.txt"
    hardware.register_user_overlay(env, "synapse-q6a-spi")
    assert env.read_text().startswith(hardware.QCOM_ENV_HEADER)
    env.write_text("overlay_prefix=qcs6490\nuser_overlays=mine\n")
    for _ in range(2):
        hardware.register_user_overlay(env, "synapse-q6a-spi")
    text = env.read_text()
    assert "overlay_prefix=qcs6490\n" in text
    assert text.count("user_overlays=") == 1
    assert "user_overlays=mine synapse-q6a-spi\n" in text


@pytest.mark.skipif(not HAS_DTC, reason="Device tree compiler required")
def test_qcom_spi_survives_armbian_dtb_rebuild(tmp_path):
    source = tmp_path / "base.dts"
    source.write_text("""/dts-v1/;
/ { compatible = "radxa,dragon-q6a", "qcom,qcm6490";
    tlmm: gpio { gpio-controller; #gpio-cells = <2>; };
    soc@0 { geniqup@ac0000 {
        spi12: spi@a90000 { #address-cells = <1>; #size-cells = <0>; status = "disabled"; };
    }; };
};""")
    root = tmp_path / "root"
    (root / "boot").mkdir(parents=True)
    pristine = tmp_path / "pristine.dtb"
    hardware.compile_overlay(source, pristine)
    tree = root / "boot/armbian-dtb-6.18"
    tree.write_bytes(pristine.read_bytes())
    (root / "boot/armbianEnv.txt").write_text("overlay_prefix=qcs6490\n")
    hardware.configure_qcom(root, "radxa-dragon-q6a")
    # What armbian-grub-with-dtb does on a kernel install or armbianEnv.txt
    # change: start again from the pristine kernel DTB and apply user_overlays.
    env = (root / "boot/armbianEnv.txt").read_text()
    names = next(
        line.split("=", 1)[1].split()
        for line in env.splitlines()
        if line.startswith("user_overlays=")
    )
    dtbos = [root / "boot/overlay-user" / f"{name}.dtbo" for name in names]
    hardware.merge_overlay(pristine, dtbos, tree)
    status = subprocess.check_output(
        ["fdtget", "-t", "s", str(tree), hardware.QCOM["radxa-dragon-q6a"]["spi_path"], "status"],
        text=True,
    ).strip()
    assert status == "okay"


def test_pwm_gpio_is_required_only_when_the_profile_asks_for_it(tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    config = boot / "config-6.6"
    config.write_text(
        "\n".join(
            f"CONFIG_{name}=y"
            for name in ("GPIO_CDEV", "SPI_SPIDEV", "PWM", "SYSFS")
        )
    )
    hardware.check_kernel(tmp_path, gadget=False)
    with pytest.raises(ValueError, match="GPIO PWM"):
        hardware.check_kernel(tmp_path, gadget=False, require_pwm_gpio=True)


def test_shared_pi_firmware_respects_model_filters_and_enables_cm4_usb_c(
    tmp_path, monkeypatch
):
    boot = tmp_path / "boot/firmware"
    (boot / "overlays").mkdir(parents=True)
    for names in hardware.PI_DTBS.values():
        (boot / names[0]).touch()
    for name in ("dwc2", "disable-bt", "uart0-pi5", "pwm-2chan"):
        (boot / "overlays" / f"{name}.dtbo").touch()
    (boot / "config.txt").write_text(
        "[cm4]\notg_mode=1\n[cm5]\ndtoverlay=dwc2,dr_mode=host\n[all]\nkernel=vmlinuz\n"
    )
    (boot / "cmdline.txt").write_text(
        "console=serial0,115200 console=tty1 root=UUID=keep rootwait\n"
    )
    monkeypatch.setattr(
        hardware,
        "compile_overlay",
        lambda source, destination: destination.write_bytes(b"test overlay"),
    )
    monkeypatch.setattr(hardware, "merge_overlay", lambda *args: None)
    hardware.configure_pi(tmp_path)
    hardware.configure_pi(tmp_path)
    text = (boot / "config.txt").read_text()
    assert text.count("# BEGIN SYNAPSE") == 1
    assert "i2c_arm=on" not in text

    def settings_for(filters):
        active = True
        result = []
        for line in text.splitlines():
            if line.startswith("["):
                active = line[1:-1] in filters or line == "[all]"
            elif active and line and not line.startswith("#"):
                result.append(line)
        return result

    pi4 = settings_for({"pi4", "board-type=0x11"})
    cm4 = settings_for({"pi4", "cm4"})
    cm5 = settings_for({"pi5", "cm5"})
    assert pi4.count("dtoverlay=dwc2,dr_mode=peripheral") == 1
    assert "otg_mode=0" in cm4
    assert cm4.count("dtoverlay=dwc2,dr_mode=peripheral") == 1
    assert cm5.count("dtoverlay=synapse-rp1-pwm") == 1
    assert cm5.count("dtoverlay=uart0-pi5") == 1
    assert cm5.count("dtoverlay=dwc2,dr_mode=peripheral") == 1
    assert (
        boot / "cmdline.txt"
    ).read_text() == "console=tty1 root=UUID=keep rootwait\n"


def test_gadget_boards_require_configfs_rndis_and_ecm(tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    base = [f"CONFIG_{n}=y" for n in ("GPIO_CDEV", "SPI_SPIDEV", "PWM", "SYSFS")]
    config = boot / "config-6.1"
    # The legacy g_ether module alone is no longer enough.
    config.write_text("\n".join(base + ["CONFIG_USB_ETH=m", "CONFIG_USB_ETH_RNDIS=y"]))
    with pytest.raises(ValueError, match="CONFIG_USB_CONFIGFS"):
        hardware.check_kernel(tmp_path)
    config.write_text(
        "\n".join(
            base
            + [
                "CONFIG_USB_CONFIGFS=m",
                "CONFIG_USB_CONFIGFS_RNDIS=y",
                "CONFIG_USB_CONFIGFS_ECM=y",
            ]
        )
    )
    hardware.check_kernel(tmp_path)


def test_usb_gadget_presents_windows_rndis_descriptors():
    script = (hardware.HERE / "synapse-usb-ethernet").read_text()
    # Legacy g_ether has no MS OS descriptors: Windows binds it as a COM port.
    assert "modprobe g_ether" not in script
    for needle in (
        "echo 1 > os_desc/use",
        "echo MSFT100 > os_desc/qw_sign",
        "echo RNDIS > functions/rndis.usb0/os_desc/interface.rndis/compatible_id",
        "echo 5162001 > functions/rndis.usb0/os_desc/interface.rndis/sub_compatible_id",
        "ln -s configs/c.1 os_desc/",
        "ln -s functions/rndis.usb0 configs/c.1/",
        "ln -s functions/ecm.usb0 configs/c.2/",
    ):
        assert needle in script, needle
    # Windows only ever selects configuration 1, so RNDIS must be in it.
    assert script.index("configs/c.1/") < script.index("functions/ecm.usb0")


def test_gadget_network_wins_over_netplan_usb_match():
    network = hardware.HERE / "network"
    files = sorted(p.name for p in network.iterdir() if p.suffix == ".network")
    gadget = [n for n in files if "gadget" in n]
    assert gadget == ["05-synapse-usb-gadget.network"]
    # networkd takes the first match by file name across directories; netplan
    # writes 10-netplan-*.network for its usb* DHCP-client match.
    assert gadget[0] < "10-netplan-"
    text = (network / gadget[0]).read_text()
    assert "Driver=g_ether" in text and "Name=" not in text
    assert "Address=10.12.194.1/28" in text and "DHCPServer=yes" in text


def test_pi_overlay_restore_runs_after_armbian_overlay_refresh(tmp_path, monkeypatch):
    # Armbian's bcm2711 hooks recreate /boot/firmware/overlays on kernel
    # install and removal; run-parts orders hooks by C-locale name.
    boot = tmp_path / "boot/firmware"
    (boot / "overlays").mkdir(parents=True)
    for names in hardware.PI_DTBS.values():
        (boot / names[0]).touch()
    for name in ("dwc2", "disable-bt", "uart0-pi5", "pwm-2chan"):
        (boot / "overlays" / f"{name}.dtbo").touch()
    (boot / "config.txt").write_text("[all]\n")
    (boot / "cmdline.txt").write_text("console=tty1 root=UUID=keep rootwait\n")
    armbian = {
        "etc/kernel/postinst.d": ["initramfs-tools", "z51-raspi-firmware", "zzz-copy-new-files"],
        "etc/kernel/postrm.d": ["initramfs-tools", "zzz-remove-old-files"],
    }
    for directory, names in armbian.items():
        (tmp_path / directory).mkdir(parents=True)
        for name in names:
            (tmp_path / directory / name).touch()
    monkeypatch.setattr(
        hardware,
        "compile_overlay",
        lambda source, destination: destination.write_bytes(b"test overlay"),
    )
    monkeypatch.setattr(hardware, "merge_overlay", lambda *args: None)
    hardware.configure_pi(tmp_path)
    for directory in armbian:
        hooks = sorted(p.name for p in (tmp_path / directory).iterdir())
        assert hooks[-1] == hardware.PI_OVERLAY_HOOK
        hook = tmp_path / directory / hardware.PI_OVERLAY_HOOK
        assert "synapse-rp1-pwm.dtbo" in hook.read_text()
        if os.name == "posix":
            assert hook.stat().st_mode & 0o111
