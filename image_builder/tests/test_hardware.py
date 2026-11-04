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
    assert len(build.image_targets()) == 4


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
        hardware.configure_q6a_dtb(base, overlay)

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
        hardware.configure_q6a_dtb(base, overlay)


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
