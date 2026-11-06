# Pi 5 GPIO Controller

A simple desktop app to control the Raspberry Pi 5's GPIO pins with the mouse:

- **Click a pin on the 40-pin header diagram**, then press the big **ON/OFF** button (the pin is switched to output automatically).
- **Timer**: set a period (e.g. `10` ms) and the pin toggles ON/OFF by itself.
- Input pins show their live HIGH/LOW level.
- **ALL OFF** button, a simulation mode, and safety prompts for pins with special functions.

> **Status:** written and unit-tested on a development PC using a mock GPIO backend and fake `gpiod`/sysfs layers.
> **It has NOT been run on a real Raspberry Pi 5 yet** (no Pi was available to the author). Please report any problems.

## Install (on the Pi 5, Raspberry Pi OS Bookworm 64-bit desktop)

```bash
# download the release tarball from the Releases page, then:
tar xzf pi5-gpio-controller-0.1.0.tar.gz
cd pi5-gpio-controller-0.1.0
./install.sh
```

`install.sh` installs `python3-tk` and the libgpiod Python binding via apt, creates a private virtualenv
in `~/.local/share/pi5-gpio`, and adds a menu entry (*Pi 5 GPIO Controller*) plus a `pi5-gpio` command.
You need to be in the `gpio` group (the default user already is): `sudo usermod -aG gpio $USER`, then log out/in.

### Standalone Linux binary (no Python install needed) - Pi 5, arm64
Download `pi5-gpio-linux-arm64.tar.gz` from the Releases page (built in CI on Debian Bookworm, same base as Raspberry Pi OS Bookworm):

```bash
tar xzf pi5-gpio-linux-arm64.tar.gz
./pi5-gpio              # opens the desktop GUI using the Pi's real GPIO
./pi5-gpio --mock       # simulation
```
You may need `sudo apt install libgpiod2 python3-tk`-style system libraries only if the binary reports a missing library; Tk and libgpiod are bundled when possible.

### Control from Windows (or any PC) in a browser
Run the agent on the Pi (it serves a web page with the same pin diagram, toggles and timers):

```bash
./pi5-gpio --serve --host 0.0.0.0 --token YOUR-SECRET     # token optional on localhost; printed if omitted
```
Then on Windows open `http://<pi-address>:8765/` (e.g. `http://raspberrypi.local:8765/`) and enter the token.
Alternatively use the desktop GUI remotely: `py -m pi5gpio --remote http://raspberrypi.local:8765 --token YOUR-SECRET`.

Security: traffic is **not encrypted** and the token is a shared secret - use it only on a trusted home network, or tunnel through SSH
(`ssh -L 8765:localhost:8765 pi@raspberrypi.local`, then run the agent with the default `--host 127.0.0.1` and browse to `http://localhost:8765/`).
If the browser/GUI stops talking to the Pi for 5 s, the agent drives all outputs LOW and stops all timers. `--host` other than localhost requires a token.

Run: `pi5-gpio`   |   Try without touching hardware: `pi5-gpio --mock`

Options: `--backend {auto,gpiod,mock}`, `--chip /dev/gpiochip4`. With `auto` (default) the app falls back to the
simulation if libgpiod is unavailable; a yellow **SIMULATION** banner is shown so you always know.

### How it talks to the pins
The Pi 5 routes the header through the RP1 chip, so the old `RPi.GPIO` library **does not work**. This app uses
**libgpiod** (`/dev/gpiochipN`, auto-detected by the chip label `pinctrl-rp1`, normally `gpiochip4`).
Both the 1.x binding (`python3-libgpiod` on Bookworm) and the 2.x API are supported.

## Using the timer (and its honest limits)
`Period 10 ms, Duty 50%` = 5 ms ON, 5 ms OFF (100 Hz). The GUI shows measured lateness.

| Need | Use |
|------|-----|
| Blink an LED, relay, slow signals (>= ~50 ms) | Software timer - fine |
| 1-20 ms periods | Software timer works but has **jitter** (typically tens to hundreds of µs, can spike to several ms when the Pi is busy). Not suitable for servos or protocols. |
| < 1 ms, or accurate/stable frequency | **Hardware PWM** (GPIO 12, 13, 18, 19) |

Minimum software period is 1 ms. The software timer is Python user-space code on a non-realtime OS; do not rely on it for precise timing.

### Enabling hardware PWM (optional)
Hardware PWM uses the kernel sysfs PWM interface. Add to `/boot/firmware/config.txt` and reboot:

```
dtoverlay=pwm-2chan
```
(GPIO12 + GPIO13 with the default overlay settings; see `dtoverlay -h pwm-2chan` / the Raspberry Pi
documentation for choosing GPIO18/19.) The app looks for a `/sys/class/pwm/pwmchip*` with 4 channels and maps
GPIO12/13/18/19 to channels 0-3. This mapping and overlay are based on documentation, **not verified on hardware**;
if the *Start HW PWM* button stays disabled, the overlay isn't active.

## Safety and wiring
- **GPIO is 3.3 V only.** Connecting 5 V to any GPIO pin can destroy the Pi. Pins 2 and 4 are 5 V *power* pins and are not controllable.
- Each pin can supply only a few mA (absolute max ~16 mA per pin, ~50 mA total). **Never drive a motor, relay coil, or buzzer directly.** Use a transistor/MOSFET or relay board.
- LED wiring: `GPIO pin -> 330 Ω resistor -> LED anode (long leg); LED cathode -> GND (e.g. pin 6)`.
- Common ground: any external circuit must share GND with the Pi.
- Pins with special functions are marked `*` (I2C GPIO2/3, UART GPIO14/15, SPI GPIO7-11). The app asks for confirmation before driving them. GPIO0/1 (ID EEPROM, pins 27/28) are not controllable at all.
- At start-up **no pin is claimed or driven**. On close (window, Ctrl+C, SIGTERM) every timer/PWM is stopped, outputs are driven LOW and lines released. If the process is killed hard (`kill -9`, power loss) the kernel releases the lines, but the resulting pin level depends on the hardware; verify before connecting sensitive loads.
- If another program already owns a pin, the app shows an error instead of taking it over.

## Development
```bash
python -m pip install pytest
python -m pytest            # GUI tests are skipped automatically when no display exists
python -m pi5gpio --mock    # run the GUI in simulation
python tools/build_release.py   # builds dist/pi5-gpio-controller-<ver>.tar.gz
```
CI (`.github/workflows/ci.yml`) runs the tests on Ubuntu under Xvfb (headless, no screenshots).

Layout: `pins.py` (header map), `backends.py` (mock + libgpiod), `controller.py` (modes, timers, safety),
`hwpwm.py` (sysfs PWM), `gui.py` (Tk).

## Limitations
- Not tested on real hardware (libgpiod calls verified only against fake modules; GUI only on a Windows PC with the mock backend).
- No `.deb` package; install is via `install.sh` / tarball.
- Software timer accuracy depends on system load; no realtime scheduling is attempted.
- Pull-ups/downs and edge events are not exposed yet.

License: MIT
