"""Tests for the libgpiod backend using fake 'gpiod' modules (v1 and v2 APIs)."""
import sys
import types

import pytest

from pi5gpio.backends import BackendError, GpiodBackend


def fake_v2():
    log = []
    m = types.ModuleType("gpiod")
    line = types.ModuleType("gpiod.line")

    class Direction:
        INPUT, OUTPUT = "in", "out"

    class Value:
        ACTIVE, INACTIVE = 1, 0

    line.Direction, line.Value = Direction, Value

    class Info:
        label = "pinctrl-rp1"

    class Chip:
        def __init__(self, path):
            self.path = path

        def get_info(self):
            return Info()

        def close(self):
            pass

    class LineSettings:
        def __init__(self, direction, output_value=0):
            self.direction, self.output_value = direction, output_value

    class Req:
        def __init__(self, config):
            self.config, self.vals = config, {k: v.output_value for k, v in config.items()}

        def set_value(self, bcm, v):
            self.vals[bcm] = v
            log.append(("set", bcm, v))

        def get_value(self, bcm):
            return self.vals[bcm]

        def release(self):
            log.append(("release",))

    def request_lines(path, consumer, config):
        return Req(config)

    m.Chip, m.LineSettings, m.request_lines, m.line = Chip, LineSettings, request_lines, line
    return m, line, log


def fake_v1():
    log = []
    m = types.ModuleType("gpiod")
    m.LINE_REQ_DIR_OUT, m.LINE_REQ_DIR_IN = 1, 2

    class Line:
        def __init__(self):
            self.v = 0

        def request(self, consumer, type, default_vals=None):
            self.v = (default_vals or [0])[0]

        def set_value(self, v):
            self.v = v
            log.append(("set", v))

        def get_value(self):
            return self.v

        def release(self):
            log.append(("release",))

    class Chip:
        def __init__(self, path):
            self.lines = {}

        def label(self):
            return "pinctrl-rp1"

        def get_line(self, n):
            return self.lines.setdefault(n, Line())

        def close(self):
            pass

    m.Chip = Chip
    return m, log


@pytest.mark.parametrize("version", [1, 2])
def test_gpiod_backend(monkeypatch, version):
    monkeypatch.setattr("glob.glob", lambda p: ["/dev/gpiochip0", "/dev/gpiochip4"])
    if version == 2:
        m, line, log = fake_v2()
        monkeypatch.setitem(sys.modules, "gpiod.line", line)
    else:
        m, log = fake_v1()
    monkeypatch.setitem(sys.modules, "gpiod", m)
    b = GpiodBackend()
    assert b.chip_path == "/dev/gpiochip0"  # first chip whose label contains rp1
    b.request_output(17, True)
    assert b.read(17) is True
    b.write(17, False)
    assert b.read(17) is False
    b.write(17, True)
    b.request_input(22)
    assert b.read(22) in (True, False)
    with pytest.raises(BackendError):
        b.write(22, True)  # input
    b.release_all()
    assert b.requested() == []
    # output driven low before release
    low = ("set", 17, 0) if version == 2 else ("set", 0)
    assert log[-3:] == [low, ("release",), ("release",)]


def test_missing_gpiod(monkeypatch):
    monkeypatch.setitem(sys.modules, "gpiod", None)
    with pytest.raises(BackendError):
        GpiodBackend()
