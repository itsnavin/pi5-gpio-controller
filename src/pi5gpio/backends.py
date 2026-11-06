"""GPIO backends: a mock for development/tests and a libgpiod one for the real Pi 5."""
import glob
import threading
import time
from typing import Dict, List, Optional, Tuple

CONSUMER = "pi5-gpio-controller"


class BackendError(RuntimeError):
    pass


class Backend:
    name = "base"
    simulated = False

    def request_output(self, bcm: int, value: bool = False) -> None:
        raise NotImplementedError

    def request_input(self, bcm: int) -> None:
        raise NotImplementedError

    def write(self, bcm: int, value: bool) -> None:
        raise NotImplementedError

    def read(self, bcm: int) -> bool:
        raise NotImplementedError

    def release(self, bcm: int) -> None:
        raise NotImplementedError

    def release_all(self) -> None:
        for bcm in list(self.requested()):
            try:
                self.release(bcm)
            except Exception:  # keep releasing the rest
                pass

    def requested(self) -> List[int]:
        raise NotImplementedError


class MockBackend(Backend):
    """In-memory GPIO. Inputs can be driven with set_external()."""

    name = "mock"
    simulated = True

    def __init__(self):
        self._lock = threading.Lock()
        self._lines: Dict[int, Tuple[str, bool]] = {}  # bcm -> (dir, value)
        self._external: Dict[int, bool] = {}
        self.events: List[Tuple[float, int, bool]] = []  # (time, bcm, value) writes

    def request_output(self, bcm, value=False):
        with self._lock:
            self._lines[bcm] = ("out", bool(value))
            self.events.append((time.perf_counter(), bcm, bool(value)))

    def request_input(self, bcm):
        with self._lock:
            self._lines[bcm] = ("in", False)

    def write(self, bcm, value):
        with self._lock:
            if self._lines.get(bcm, ("", 0))[0] != "out":
                raise BackendError(f"GPIO{bcm} is not an output")
            self._lines[bcm] = ("out", bool(value))
            self.events.append((time.perf_counter(), bcm, bool(value)))

    def read(self, bcm):
        with self._lock:
            d = self._lines.get(bcm)
            if d is None:
                raise BackendError(f"GPIO{bcm} is not requested")
            return d[1] if d[0] == "out" else self._external.get(bcm, False)

    def release(self, bcm):
        with self._lock:
            self._lines.pop(bcm, None)

    def requested(self):
        with self._lock:
            return list(self._lines)

    def set_external(self, bcm: int, value: bool) -> None:
        with self._lock:
            self._external[bcm] = bool(value)


class GpiodBackend(Backend):
    """libgpiod backend. Supports both the v2 (python3-gpiod >= 2) and v1 (Bookworm
    python3-libgpiod 1.x) Python bindings. On the Pi 5 the header is on the RP1 chip,
    usually /dev/gpiochip4 (found at runtime by label 'pinctrl-rp1')."""

    name = "libgpiod"

    def __init__(self, chip_path: Optional[str] = None):
        try:
            import gpiod
        except ImportError as e:
            raise BackendError(
                "Python module 'gpiod' not found. Install it with: "
                "sudo apt install python3-libgpiod   (or python3-gpiod on newer OS)") from e
        self._g = gpiod
        self._v2 = hasattr(gpiod, "request_lines")
        self.chip_path = chip_path or self._find_chip()
        try:
            self._chip = gpiod.Chip(self.chip_path)
        except Exception as e:
            raise BackendError(f"Cannot open {self.chip_path}: {e}") from e
        self._reqs: Dict[int, object] = {}
        self._dirs: Dict[int, str] = {}

    def _label(self, path: str) -> str:
        chip = self._g.Chip(path)
        try:
            if self._v2:
                return chip.get_info().label
            return chip.label() if callable(getattr(chip, "label", None)) else chip.label
        finally:
            try:
                chip.close()
            except Exception:
                pass

    def _find_chip(self) -> str:
        paths = sorted(glob.glob("/dev/gpiochip*"))
        for p in paths:
            try:
                if "rp1" in self._label(p).lower():
                    return p
            except Exception:
                continue
        for p in ("/dev/gpiochip4", "/dev/gpiochip0"):
            if p in paths:
                return p
        raise BackendError("No /dev/gpiochip* found. Is this a Raspberry Pi?")

    def _request(self, bcm, direction, value=False):
        self.release(bcm)
        g = self._g
        try:
            if self._v2:
                from gpiod.line import Direction, Value
                if direction == "out":
                    settings = g.LineSettings(
                        direction=Direction.OUTPUT,
                        output_value=Value.ACTIVE if value else Value.INACTIVE)
                else:
                    settings = g.LineSettings(direction=Direction.INPUT)
                req = g.request_lines(self.chip_path, consumer=CONSUMER,
                                      config={bcm: settings})
            else:
                req = self._chip.get_line(bcm)
                if direction == "out":
                    req.request(consumer=CONSUMER, type=g.LINE_REQ_DIR_OUT,
                                default_vals=[int(value)])
                else:
                    req.request(consumer=CONSUMER, type=g.LINE_REQ_DIR_IN)
        except Exception as e:
            raise BackendError(f"Cannot request GPIO{bcm} (in use by another program?): {e}") from e
        self._reqs[bcm] = req
        self._dirs[bcm] = direction

    def request_output(self, bcm, value=False):
        self._request(bcm, "out", value)

    def request_input(self, bcm):
        self._request(bcm, "in")

    def write(self, bcm, value):
        req = self._reqs.get(bcm)
        if req is None or self._dirs[bcm] != "out":
            raise BackendError(f"GPIO{bcm} is not an output")
        if self._v2:
            from gpiod.line import Value
            req.set_value(bcm, Value.ACTIVE if value else Value.INACTIVE)
        else:
            req.set_value(int(bool(value)))

    def read(self, bcm):
        req = self._reqs.get(bcm)
        if req is None:
            raise BackendError(f"GPIO{bcm} is not requested")
        if self._v2:
            from gpiod.line import Value
            return req.get_value(bcm) == Value.ACTIVE
        return bool(req.get_value())

    def release(self, bcm):
        req = self._reqs.pop(bcm, None)
        direction = self._dirs.pop(bcm, None)
        if req is None:
            return
        try:
            if direction == "out":  # leave the pin low rather than at an arbitrary level
                if self._v2:
                    from gpiod.line import Value
                    req.set_value(bcm, Value.INACTIVE)
                else:
                    req.set_value(0)
        except Exception:
            pass
        try:
            req.release()
        except Exception:
            pass

    def requested(self):
        return list(self._reqs)


def create_backend(kind: str = "auto", chip: Optional[str] = None) -> Backend:
    if kind == "mock":
        return MockBackend()
    if kind == "gpiod":
        return GpiodBackend(chip)
    if kind == "auto":
        try:
            return GpiodBackend(chip)
        except BackendError:
            return MockBackend()
    raise ValueError(f"unknown backend {kind!r}")
