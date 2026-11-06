import os
import time

import pytest

from pi5gpio.backends import BackendError, MockBackend
from pi5gpio.controller import Controller, SpecialPinError
from pi5gpio.hwpwm import HardwarePwm, PwmError
from pi5gpio.pins import BCM_PINS, BY_PHYSICAL, PINS


def make(pwm=None):
    b = MockBackend()
    return b, Controller(b, pwm)


def test_pin_map():
    assert len(PINS) == 40
    assert BCM_PINS == list(range(2, 28))  # GPIO0/1 are the HAT ID EEPROM
    assert BY_PHYSICAL[12].bcm == 18 and BY_PHYSICAL[40].bcm == 21
    assert BY_PHYSICAL[1].label == "3V3" and not BY_PHYSICAL[6].controllable
    assert sum(p.kind == "gnd" for p in PINS) == 8
    assert sum(p.kind == "5v" for p in PINS) == 2 and sum(p.kind == "3v3" for p in PINS) == 2


def test_nothing_requested_on_startup():
    b, c = make()
    assert b.requested() == []


def test_toggle():
    b, c = make()
    c.set_output(17)
    assert c.read(17) is False
    assert c.toggle(17) is True and b.read(17) is True
    assert c.toggle(17) is False


def test_set_value_requires_output():
    _, c = make()
    with pytest.raises(BackendError):
        c.set_value(17, True)


def test_input_reads_external():
    b, c = make()
    c.set_input(22)
    assert c.read(22) is False
    b.set_external(22, True)
    assert c.read(22) is True


def test_special_pin_needs_confirmation():
    b, c = make()
    with pytest.raises(SpecialPinError) as e:
        c.set_output(2)
    assert "I2C" in e.value.function
    assert b.requested() == []
    c.set_output(2, allow_special=True)
    assert b.requested() == [2]


def test_invalid_pin():
    _, c = make()
    with pytest.raises(ValueError):
        c.state(0)
    with pytest.raises(ValueError):
        c.state(99)


def test_timer_validation():
    _, c = make()
    with pytest.raises(ValueError):
        c.start_timer(17, 0.0005)
    with pytest.raises(ValueError):
        c.start_timer(17, 0.01, duty=1.0)
    with pytest.raises(SpecialPinError):
        c.start_timer(14, 0.01)


def test_timer_toggles_and_stops_low():
    b, c = make()
    c.start_timer(17, 0.010)  # 10 ms period => 5 ms on / 5 ms off
    time.sleep(0.35)
    stats = c.timer_stats(17)
    assert stats.error is None
    c.stop_timer(17)
    ev = [e for e in b.events if e[1] == 17][1:]
    assert len(ev) >= 10
    assert [e[2] for e in ev[:4]] == [True, False, True, False]
    assert b.read(17) is False and c.state(17).timer is None
    n = len(b.events)
    time.sleep(0.05)
    assert len(b.events) == n  # really stopped


def test_timer_period_roughly_correct():
    b, c = make()
    c.start_timer(17, 0.020, duty=0.25)
    time.sleep(0.5)
    c.stop_timer(17)
    rises = [e[0] for e in b.events if e[1] == 17 and e[2]][1:]
    gaps = [b_ - a for a, b_ in zip(rises, rises[1:])]
    avg = sum(gaps) / len(gaps)
    assert 0.018 < avg < 0.024  # generous: CI machines are noisy


def test_manual_toggle_blocked_while_timer_runs():
    _, c = make()
    c.start_timer(17, 0.01)
    with pytest.raises(BackendError):
        c.toggle(17)
    c.close()


def test_all_off_and_close():
    b, c = make()
    c.set_output(5)
    c.toggle(5)
    c.start_timer(6, 0.01)
    c.set_input(7, allow_special=True)
    c.all_off()
    assert b.read(5) is False and c.state(6).timer is None
    assert not any(e[2] for e in b.events[-2:])
    c.close()
    assert b.requested() == []
    assert all(c.state(p).mode == "unused" for p in (5, 6, 7))


def test_mode_change_stops_timer():
    b, c = make()
    c.start_timer(17, 0.01)
    c.set_input(17)
    assert c.state(17).timer is None and c.state(17).mode == "input"


# ---- hardware PWM against a fake sysfs tree
@pytest.fixture
def fake_sysfs(tmp_path):
    chip = tmp_path / "pwmchip2"
    chip.mkdir()
    (chip / "npwm").write_text("4")
    (chip / "export").write_text("")
    (chip / "unexport").write_text("")
    return tmp_path, chip


def _export_channel(chip, ch):  # emulate the kernel creating pwmN after export
    d = chip / f"pwm{ch}"
    d.mkdir(exist_ok=True)
    for f in ("period", "duty_cycle", "enable"):
        (d / f).write_text("0")
    return d


def test_hwpwm_unavailable(tmp_path):
    pwm = HardwarePwm(str(tmp_path))
    assert not pwm.available
    with pytest.raises(PwmError):
        pwm.start(18, 100, 0.5)


def test_hwpwm_start_stop(fake_sysfs):
    root, chip = fake_sysfs
    d = _export_channel(chip, 2)
    pwm = HardwarePwm(str(root))
    assert pwm.available and pwm.supports(18) and not pwm.supports(17)
    pwm.start(18, 100, 0.25)
    assert (d / "period").read_text() == "10000000"
    assert (d / "duty_cycle").read_text() == "2500000"
    assert (d / "enable").read_text() == "1"
    pwm.stop(18)
    assert (d / "enable").read_text() == "0"
    with pytest.raises(PwmError):
        pwm.start(17, 100, 0.5)
    with pytest.raises(PwmError):
        pwm.start(18, 0, 0.5)


def test_controller_hw_pwm(fake_sysfs):
    root, chip = fake_sysfs
    d = _export_channel(chip, 0)
    b, c = make(HardwarePwm(str(root)))
    c.set_output(12)
    c.start_hw_pwm(12, 1000, 0.5)
    assert 12 not in b.requested()  # PWM block owns the pin
    assert c.state(12).timer == "hardware" and (d / "enable").read_text() == "1"
    c.all_off()
    assert (d / "enable").read_text() == "0" and c.state(12).timer is None
    with pytest.raises(PwmError):
        c.start_hw_pwm(17, 100)
    c.close()
