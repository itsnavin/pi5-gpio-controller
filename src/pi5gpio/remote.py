"""Remote control: an HTTP agent that runs on the Pi and a client with the Controller interface.

Protocol: POST /api/call {"method": ..., "args": [...], "kwargs": {...}} and GET /api/snapshot,
both with the header "Authorization" set to "Bearer" + a space + the token. No TLS: use only on a trusted network or
through an SSH tunnel (ssh -L 8765:localhost:8765 pi@pi5.local).
"""
import hmac
import json
import secrets
import threading
import time
import types
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .backends import BackendError
from .controller import Controller, PinState, SpecialPinError, TimerStats
from .hwpwm import PwmError
from .pins import BCM_PINS, PINS
from .webui import INDEX_HTML

DEFAULT_PORT = 8765
WATCHDOG_S = 5.0
CALLS = {"set_output", "set_input", "release", "set_value", "toggle", "start_timer",
         "start_hw_pwm", "stop_timer", "all_off"}
ERRORS = {"BackendError": BackendError, "PwmError": PwmError, "ValueError": ValueError}


def snapshot(ctl: Controller) -> dict:
    pins = {}
    with ctl._lock:
        for bcm in BCM_PINS:
            st = ctl.state(bcm)
            if st.mode == "input":
                try:
                    ctl.read(bcm)
                except BackendError:
                    pass
            stats = ctl.timer_stats(bcm)
            pins[str(bcm)] = {
                "mode": st.mode, "value": st.value, "timer": st.timer,
                "period_s": st.period_s, "duty": st.duty,
                "stats": None if stats is None else {
                    "toggles": stats.toggles, "max_late_us": stats.max_late_us,
                    "total_late_us": stats.total_late_us, "error": stats.error},
            }
    return {"pins": pins, "simulated": ctl.backend.simulated, "backend": ctl.backend.name,
            "pwm_available": bool(ctl.pwm and ctl.pwm.available)}


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64


def _make_handler(ctl, token, state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _auth(self):
            given = self.headers.get("Authorization", "")
            if hmac.compare_digest(given.encode(), ("Bearer " + token).encode()):
                state["last"] = time.monotonic()
                state["seen"] = True
                return True
            self._send(401, {"error": "unauthorized", "type": "Auth"})
            return False

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                body = INDEX_HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/layout":
                return self._send(200, [{"physical": p.physical, "label": p.label, "bcm": p.bcm,
                                         "special": p.special, "kind": p.kind} for p in PINS])
            if self.path != "/api/snapshot":
                return self._send(404, {"error": "not found", "type": "NotFound"})
            if self._auth():
                self._send(200, snapshot(ctl))

        def do_POST(self):
            if self.path != "/api/call":
                return self._send(404, {"error": "not found", "type": "NotFound"})
            if not self._auth():
                return
            try:
                n = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(min(n, 65536)))
                method = req["method"]
                if method not in CALLS:
                    return self._send(200, {"error": "unknown method", "type": "ValueError"})
                result = getattr(ctl, method)(*req.get("args", []), **req.get("kwargs", {}))
                self._send(200, {"result": result if isinstance(result, (bool, type(None))) else None})
            except SpecialPinError as e:
                self._send(200, {"error": str(e), "type": "SpecialPinError",
                                 "bcm": e.bcm, "function": e.function})
            except (BackendError, PwmError, ValueError, KeyError, TypeError) as e:
                t = type(e).__name__
                self._send(200, {"error": str(e), "type": t if t in ERRORS else "ValueError"})

    return Handler


class Server:
    """Agent wrapping a Controller. A watchdog drives everything low if the client vanishes."""

    def __init__(self, ctl: Controller, host="127.0.0.1", port=DEFAULT_PORT,
                 token: Optional[str] = None, watchdog_s=WATCHDOG_S):
        self.ctl = ctl
        self.token = token or secrets.token_urlsafe(16)
        self.watchdog_s = watchdog_s
        self._state = {"last": time.monotonic(), "seen": False}
        self.httpd = _HTTPServer((host, port), _make_handler(ctl, self.token, self._state))
        self.port = self.httpd.server_address[1]
        self._stop = threading.Event()
        self._wd = threading.Thread(target=self._watch, daemon=True)

    def _watch(self):
        while not self._stop.wait(0.2):
            s = self._state
            if s["seen"] and time.monotonic() - s["last"] > self.watchdog_s:
                s["seen"] = False
                self.ctl.all_off()

    def start(self):
        self._wd.start()
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def serve_forever(self):
        self._wd.start()
        try:
            self.httpd.serve_forever()
        finally:
            self.close()

    def close(self):
        self._stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.ctl.close()


class RemoteController:
    """Drop-in replacement for Controller (as used by the GUI) talking to a Server."""

    def __init__(self, url: str, token: str, timeout=3.0, cache_s=0.05):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.cache_s = cache_s
        self._states = {b: PinState() for b in BCM_PINS}
        self._stats = {}
        self._at = 0.0
        self.backend = types.SimpleNamespace(simulated=False, name="remote")
        self.pwm = types.SimpleNamespace(available=False)
        self._snapshot(force=True)  # fail fast on a bad URL/token

    def _request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(self.url + path, data=data, method="GET" if data is None else "POST")
        req.add_header("Authorization", "Bearer " + self.token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        # Every call is idempotent, so transient connection failures are retried.
        last = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise BackendError("Remote rejected the token (unauthorized)") from e
                raise BackendError(f"Remote error: HTTP {e.code}") from e
            except (urllib.error.URLError, OSError, ValueError) as e:
                last = e
                time.sleep(0.05 * (attempt + 1))
        raise BackendError(f"Cannot reach {self.url}: {last}") from last

    def _snapshot(self, force=False):
        if not force and time.monotonic() - self._at < self.cache_s:
            return
        snap = self._request("/api/snapshot")
        for k, d in snap["pins"].items():
            st = self._states[int(k)]
            st.mode, st.value, st.timer = d["mode"], d["value"], d["timer"]
            st.period_s, st.duty = d["period_s"], d["duty"]
            self._stats[int(k)] = TimerStats(**d["stats"]) if d["stats"] else None
        self.backend.simulated = snap["simulated"]
        self.backend.name = "remote/" + snap["backend"]
        self.pwm.available = snap["pwm_available"]
        self._at = time.monotonic()

    def _call(self, method, *args, **kwargs):
        resp = self._request("/api/call", {"method": method, "args": list(args), "kwargs": kwargs})
        self._at = 0.0
        if "error" in resp:
            if resp["type"] == "SpecialPinError":
                raise SpecialPinError(resp["bcm"], resp["function"])
            raise ERRORS.get(resp["type"], BackendError)(resp["error"])
        return resp.get("result")

    def state(self, bcm):
        if bcm not in self._states:
            raise ValueError(f"GPIO{bcm} is not a controllable pin")
        self._snapshot()
        return self._states[bcm]

    def read(self, bcm):
        return self.state(bcm).value

    def timer_stats(self, bcm):
        self._snapshot()
        return self._stats.get(bcm)

    def set_output(self, bcm, allow_special=False):
        self._call("set_output", bcm, allow_special=allow_special)

    def set_input(self, bcm, allow_special=False):
        self._call("set_input", bcm, allow_special=allow_special)

    def release(self, bcm):
        self._call("release", bcm)

    def set_value(self, bcm, value):
        self._call("set_value", bcm, bool(value))

    def toggle(self, bcm):
        self._snapshot(force=True)
        new = not self._states[bcm].value
        self._call("set_value", bcm, new)
        return new

    def start_timer(self, bcm, period_s, duty=0.5, allow_special=False):
        self._call("start_timer", bcm, period_s, duty, allow_special=allow_special)

    def start_hw_pwm(self, bcm, freq_hz, duty=0.5, allow_special=False):
        self._call("start_hw_pwm", bcm, freq_hz, duty, allow_special=allow_special)

    def stop_timer(self, bcm):
        self._call("stop_timer", bcm)
        return self.state(bcm)

    def all_off(self):
        self._call("all_off")

    def close(self):
        """Leave the Pi safe when the GUI closes."""
        try:
            self._call("all_off")
        except BackendError:
            pass
