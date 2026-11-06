"""Entry point: python -m pi5gpio  /  pi5-gpio"""
import argparse
import atexit
import signal
import sys

from . import __version__
from .backends import BackendError, create_backend
from .controller import Controller
from .hwpwm import HardwarePwm


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pi5-gpio", description="GUI GPIO controller for Raspberry Pi 5")
    ap.add_argument("--backend", choices=["auto", "gpiod", "mock"], default="auto",
                    help="auto = libgpiod if available else simulation (default)")
    ap.add_argument("--mock", action="store_true", help="shortcut for --backend mock")
    ap.add_argument("--chip", help="GPIO chip path, e.g. /dev/gpiochip4 (auto-detected by default)")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)
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
    from .gui import run
    run(ctl)
    return 0


if __name__ == "__main__":
    sys.exit(main())
