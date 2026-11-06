"""Hardware PWM through the Linux sysfs PWM interface (/sys/class/pwm).

Requires enabling the kernel overlay first, e.g. in /boot/firmware/config.txt:
    dtoverlay=pwm-2chan,pin=18,func=2,pin2=19,func2=2
(or `dtoverlay=pwm,pin=12,func=4` etc. -- see README) and a reboot. Not validated on
real hardware by the author of this code."""
import glob
import os
from typing import Optional

from .pins import HW_PWM_CHANNELS


class PwmError(RuntimeError):
    pass


class HardwarePwm:
    def __init__(self, root: str = "/sys/class/pwm"):
        self.root = root
        self.chip = self._find_chip()
        self._active = {}  # bcm -> channel

    def _find_chip(self) -> Optional[str]:
        best = None
        for chip in sorted(glob.glob(os.path.join(self.root, "pwmchip*"))):
            try:
                with open(os.path.join(chip, "npwm")) as f:
                    n = int(f.read().strip())
            except (OSError, ValueError):
                continue
            if n >= 4:
                return chip
            best = best or chip
        return best

    @property
    def available(self) -> bool:
        return self.chip is not None

    @staticmethod
    def supports(bcm: int) -> bool:
        return bcm in HW_PWM_CHANNELS

    def _w(self, path, value):
        try:
            with open(path, "w") as f:
                f.write(str(value))
        except OSError as e:
            raise PwmError(f"Cannot write {value} to {path}: {e}") from e

    def start(self, bcm: int, freq_hz: float, duty: float) -> None:
        if not self.available:
            raise PwmError("No PWM chip found. Enable the pwm overlay in config.txt and reboot.")
        if bcm not in HW_PWM_CHANNELS:
            raise PwmError(f"GPIO{bcm} has no hardware PWM")
        if not (0 < freq_hz <= 10_000_000):
            raise PwmError("Frequency must be between 0 and 10 MHz")
        if not (0 <= duty <= 1):
            raise PwmError("Duty must be between 0 and 1")
        ch = HW_PWM_CHANNELS[bcm]
        d = os.path.join(self.chip, f"pwm{ch}")
        if not os.path.isdir(d):
            self._w(os.path.join(self.chip, "export"), ch)
        period = max(1, int(round(1e9 / freq_hz)))
        self._w(os.path.join(d, "duty_cycle"), 0)
        self._w(os.path.join(d, "period"), period)
        self._w(os.path.join(d, "duty_cycle"), int(period * duty))
        self._w(os.path.join(d, "enable"), 1)
        self._active[bcm] = ch

    def stop(self, bcm: int) -> None:
        ch = self._active.pop(bcm, None)
        if ch is None:
            return
        d = os.path.join(self.chip, f"pwm{ch}")
        try:
            self._w(os.path.join(d, "enable"), 0)
        finally:
            try:
                self._w(os.path.join(self.chip, "unexport"), ch)
            except PwmError:
                pass

    def stop_all(self) -> None:
        for bcm in list(self._active):
            try:
                self.stop(bcm)
            except PwmError:
                pass

    def active(self):
        return list(self._active)
