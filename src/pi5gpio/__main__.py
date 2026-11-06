"""Entry point: python -m pi5gpio  /  pi5-gpio"""
import argparse
import atexit
import os
import signal
import sys

from . import __version__
from .backends import BackendError, create_backend
from .controller import Controller
from .hwpwm import HardwarePwm
from .remote import DEFAULT_PORT, RemoteController, Server


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pi5-gpio", description="GUI GPIO controller for Raspberry Pi 5")
    ap.add_argument("--backend", choices=["auto", "gpiod", "mock"], default="auto",
                    help="auto = libgpiod if available else simulation (default)")
    ap.add_argument("--mock", action="store_true", help="shortcut for --backend mock")
    ap.add_argument("--chip", help="GPIO chip path, e.g. /dev/gpiochip4 (auto-detected by default)")
    ap.add_argument("--serve", action="store_true",
                    help="run headless on the Pi so a GUI on another computer can control it")
    ap.add_argument("--host", default="127.0.0.1",
                    help="address for --serve (default 127.0.0.1; use 0.0.0.0 for the LAN)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--remote", metavar="URL", help="control a Pi running --serve, e.g. http://pi5.local:8765")
    ap.add_argument("--token", default=os.environ.get("PI5GPIO_TOKEN"),
                    help="shared secret (or env PI5GPIO_TOKEN); --serve generates one if omitted")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)
    if args.remote:
        if not args.token:
            print("error: --remote needs --token (or PI5GPIO_TOKEN)", file=sys.stderr)
            return 1
        try:
            ctl = RemoteController(args.remote, args.token)
        except BackendError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        from .gui import run
        run(ctl)
        return 0
    kind = "mock" if args.mock else args.backend
    try:
        backend = create_backend(kind, args.chip)
    except BackendError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    ctl = Controller(backend, HardwarePwm())
    atexit.register(ctl.close)

    def _sig(signum, frame):
        ctl.close()
        sys.exit(128 + signum)

    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, _sig)
    print(f"Backend: {backend.name}" + (" (SIMULATION)" if backend.simulated else ""))
    if args.serve:
        if args.host not in ("127.0.0.1", "localhost", "::1") and not args.token:
            print("error: --host exposing the network requires --token", file=sys.stderr)
            return 1
        srv = Server(ctl, args.host, args.port, args.token)
        print(f"Serving on http://{args.host}:{srv.port}  token: {srv.token}")
        print(f"Open it in a browser (e.g. from Windows): http://<this-Pi-address>:{srv.port}/ "
              "and enter the token when asked.")
        print("No encryption: use a trusted network or an SSH tunnel. Ctrl+C to stop "
              "(all pins are driven low and released).")
        srv.serve_forever()
        return 0
    from .gui import run
    run(ctl)
    return 0


if __name__ == "__main__":
    sys.exit(main())
