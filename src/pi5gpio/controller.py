"""Pin controller: modes, manual toggling, software timers and hardware PWM."""
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

from .backends import Backend, BackendError
from .hwpwm import HardwarePwm, PwmError
from .pins import BY_BCM

MIN_SW_PERIOD_S = 0.001  # 1 ms: below this user-space toggling is meaningless


class SpecialPinError(Exception):
    """Raised when driving a pin that has a special function without confirmation."""

    def __init__(self, bcm, function):
        super().__init__(f"GPIO{bcm} is used for {function}")
        self.bcm, self.function = bcm, function


@dataclass
class TimerStats:
    toggles: int = 0
    max_late_us: float = 0.0
    total_late_us: float = 0.0
    error: Optional[str] = None

    @property
    def avg_late_us(self):
        return self.total_late_us / self.toggles if self.toggles else 0.0


class _SoftTimer(threading.Thread):
    def __init__(self, ctl, bcm, high_s, low_s):
        super().__init__(daemon=True, name=f"timer-gpio{bcm}")
        self.ctl, self.bcm = ctl, bcm
        self.high_s, self.low_s = high_s, low_s
        self.stop_evt = threading.Event()
        self.stats = TimerStats()

    def _sleep_until(self, deadline):
        while not self.stop_evt.is_set():
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return
            if remaining > 0.002:
                self.stop_evt.wait(remaining - 0.001)
            # final <=1 ms is spun for accuracy

    def run(self):
        nxt = time.perf_counter()
        level = True
        try:
            while not self.stop_evt.is_set():
                late = (time.perf_counter() - nxt) * 1e6
                self.ctl._timer_write(self.bcm, level)
                st = self.stats
                st.toggles += 1
                st.total_late_us += max(late, 0)
                st.max_late_us = max(st.max_late_us, late)
                nxt += self.high_s if level else self.low_s
                level = not level
                self._sleep_until(nxt)
        except Exception as e:
            self.stats.error = str(e)


@dataclass
class PinState:
    mode: str = "unused"  # unused | input | output
    value: bool = False
    timer: Optional[str] = None  # None | "software" | "hardware"
    period_s: float = 0.0
    duty: float = 0.5


class Controller:
    def __init__(self, backend: Backend, pwm: Optional[HardwarePwm] = None):
        self.backend = backend
        self.pwm = pwm
        self._lock = threading.RLock()
        self._states: Dict[int, PinState] = {}
        self._timers: Dict[int, _SoftTimer] = {}
        self._closed = False

    # -- helpers
    def state(self, bcm: int) -> PinState:
        if bcm not in BY_BCM:
            raise ValueError(f"GPIO{bcm} is not a controllable pin")
        with self._lock:
            return self._states.setdefault(bcm, PinState())

    def _check_special(self, bcm, allow_special):
        fn = BY_BCM[bcm].special
        if fn and not allow_special:
            raise SpecialPinError(bcm, fn)

    def _timer_write(self, bcm, level):
        # No controller lock here: stoppers join this thread while holding it.
        self.backend.write(bcm, level)
        self._states[bcm].value = level
    # -- modes
    def set_output(self, bcm, allow_special=False):
        st = self.state(bcm)
        self._check_special(bcm, allow_special)
        with self._lock:
            self._stop_all_timers_for(bcm)
            self.backend.request_output(bcm, False)
            st.mode, st.value = "output", False

    def set_input(self, bcm, allow_special=False):
        st = self.state(bcm)
        self._check_special(bcm, allow_special)
        with self._lock:
            self._stop_all_timers_for(bcm)
            self.backend.request_input(bcm)
            st.mode, st.value = "input", False

    def release(self, bcm):
        st = self.state(bcm)
        with self._lock:
            self._stop_all_timers_for(bcm)
            self.backend.release(bcm)
            st.mode, st.value = "unused", False

    # -- manual control
    def set_value(self, bcm, value: bool):
        st = self.state(bcm)
        with self._lock:
            if st.mode != "output":
                raise BackendError(f"GPIO{bcm} is not an output")
            if st.timer:
                raise BackendError(f"GPIO{bcm} has a running timer; stop it first")
            self.backend.write(bcm, bool(value))
            st.value = bool(value)

    def toggle(self, bcm) -> bool:
        with self._lock:
            self.set_value(bcm, not self.state(bcm).value)
            return self.state(bcm).value

    def read(self, bcm) -> bool:
        st = self.state(bcm)
        with self._lock:
            if st.mode == "unused":
                raise BackendError(f"GPIO{bcm} is not requested")
            if st.timer == "hardware":
                return st.value
            st.value = self.backend.read(bcm)
            return st.value

    # -- timers
    def start_timer(self, bcm, period_s: float, duty: float = 0.5, allow_special=False):
        """Software timer: pin goes high for period*duty then low for the rest, repeatedly."""
        if not (0 < duty < 1):
            raise ValueError("duty must be between 0 and 1 (exclusive)")
        if period_s < MIN_SW_PERIOD_S:
            raise ValueError(
                f"Software timer period must be >= {MIN_SW_PERIOD_S * 1000:g} ms; "
                "use hardware PWM for faster signals")
        st = self.state(bcm)
        with self._lock:
            if st.mode != "output":
                self.set_output(bcm, allow_special)
            else:
                self._check_special(bcm, allow_special)
            self._stop_all_timers_for(bcm)
            t = _SoftTimer(self, bcm, period_s * duty, period_s * (1 - duty))
            self._timers[bcm] = t
            st.timer, st.period_s, st.duty = "software", period_s, duty
            t.start()

    def start_hw_pwm(self, bcm, freq_hz: float, duty: float = 0.5, allow_special=False):
        if self.pwm is None or not self.pwm.available:
            raise PwmError("Hardware PWM not available (enable the pwm overlay and reboot)")
        if not HardwarePwm.supports(bcm):
            raise PwmError(f"GPIO{bcm} does not support hardware PWM")
        st = self.state(bcm)
        self._check_special(bcm, allow_special)
        with self._lock:
            self._stop_all_timers_for(bcm)
            self.backend.release(bcm)  # PWM block owns the pin now
            self.pwm.start(bcm, freq_hz, duty)
            st.mode, st.timer = "output", "hardware"
            st.period_s, st.duty = 1.0 / freq_hz, duty

    def stop_timer(self, bcm):
        st = self.state(bcm)
        with self._lock:
            self._stop_all_timers_for(bcm)
        return st

    def _stop_all_timers_for(self, bcm):
        st = self._states.get(bcm)
        t = self._timers.pop(bcm, None)
        if t:
            t.stop_evt.set()
            t.join(timeout=2)
        was_hw = st is not None and st.timer == "hardware"
        if was_hw and self.pwm:
            self.pwm.stop(bcm)
            try:
                self.backend.request_output(bcm, False)
            except BackendError:
                st.mode = "unused"
        if st is not None and st.timer:
            st.timer = None
            if st.mode == "output":
                try:
                    self.backend.write(bcm, False)
                except BackendError:
                    pass
                st.value = False
    def timer_stats(self, bcm) -> Optional[TimerStats]:
        t = self._timers.get(bcm)
        return t.stats if t else None

    # -- global
    def all_off(self):
        """Stop every timer and drive every output low (outputs stay requested as outputs)."""
        with self._lock:
            for bcm, st in list(self._states.items()):
                if st.timer:
                    self._stop_all_timers_for(bcm)
                if st.mode == "output":
                    try:
                        self.backend.write(bcm, False)
                    except BackendError:
                        pass
                    st.value = False

    def close(self):
        """Stop everything, drive outputs low and release all lines."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for bcm in list(self._timers):
                self._stop_all_timers_for(bcm)
            if self.pwm:
                self.pwm.stop_all()
            self.backend.release_all()
            for st in self._states.values():
                st.mode, st.value, st.timer = "unused", False, None
