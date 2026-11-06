import time

import pytest

from pi5gpio.backends import BackendError, MockBackend
from pi5gpio.controller import Controller, SpecialPinError
from pi5gpio.remote import RemoteController, Server


@pytest.fixture
def pair():
    backend = MockBackend()
    ctl = Controller(backend)
    srv = Server(ctl, "127.0.0.1", 0, token="secret", watchdog_s=0.5)
    srv.start()
    url = f"http://127.0.0.1:{srv.port}"
    rc = RemoteController(url, "secret")
    yield backend, ctl, srv, rc, url
    srv.close()


def test_toggle_and_state(pair):
    backend, ctl, srv, rc, _ = pair
    assert rc.backend.simulated
    rc.set_output(17)
    assert rc.toggle(17) is True
    assert backend.read(17) is True and rc.state(17).value is True
    rc.set_input(22)
    backend.set_external(22, True)
    time.sleep(0.06)
    assert rc.read(22) is True


def test_bad_token(pair):
    *_, url = pair
    with pytest.raises(BackendError):
        RemoteController(url, "wrong")


def test_unreachable():
    with pytest.raises(BackendError):
        RemoteController("http://127.0.0.1:9", "x", timeout=0.5)


def test_errors_propagate(pair):
    _, _, _, rc, _ = pair
    with pytest.raises(SpecialPinError):
        rc.set_output(2)
    rc.set_output(2, allow_special=True)
    with pytest.raises(ValueError):
        rc.start_timer(17, 0.0001)
    with pytest.raises(BackendError):
        rc.set_value(5, True)  # not an output


def test_timer_and_stats(pair):
    backend, _, _, rc, _ = pair
    rc.start_timer(17, 0.010)
    time.sleep(0.3)
    assert rc.state(17).timer == "software"
    assert rc.timer_stats(17).toggles > 5
    rc.stop_timer(17)
    assert rc.state(17).timer is None and backend.read(17) is False


def test_watchdog_stops_everything(pair):
    backend, ctl, _, rc, _ = pair
    rc.set_output(17)
    rc.toggle(17)
    rc.start_timer(6, 0.01)
    time.sleep(1.2)  # no client traffic
    assert backend.read(17) is False and ctl.state(6).timer is None


def test_client_close_is_safe(pair):
    backend, _, _, rc, _ = pair
    rc.set_output(17)
    rc.toggle(17)
    rc.close()
    assert backend.read(17) is False


def test_unknown_method_rejected(pair):
    _, _, _, rc, _ = pair
    with pytest.raises(ValueError):
        rc._call("close")


def _get(url):
    import urllib.request
    last = None
    for _ in range(5):  # loopback on some dev machines resets connections spuriously
        try:
            return urllib.request.urlopen(url, timeout=3).read()
        except OSError as e:
            last = e
            time.sleep(0.1)
    raise last


def test_web_ui_served(pair):
    import json
    *_, url = pair
    page = _get(url + "/").decode()
    assert "Pi 5 GPIO" in page and "/api/snapshot" in page
    layout = json.loads(_get(url + "/api/layout"))
    assert len(layout) == 40 and layout[11]["bcm"] == 18


def test_embedded_js_is_valid():
    import re
    import shutil
    import subprocess
    import tempfile
    from pi5gpio.webui import INDEX_HTML
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    js = re.search(r"<script>(.*)</script>", INDEX_HTML, re.S).group(1)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js)
    r = subprocess.run([node, "--check", f.name], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
