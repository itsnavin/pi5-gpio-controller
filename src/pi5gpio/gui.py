"""Tkinter GUI: 40-pin header diagram + per-pin controls."""
import tkinter as tk
from tkinter import messagebox, ttk

from .backends import BackendError
from .controller import Controller, SpecialPinError
from .hwpwm import HardwarePwm, PwmError
from .pins import BY_BCM, PINS

SAFETY_TEXT = ("GPIO is 3.3 V ONLY - never connect 5 V to a GPIO pin.  "
               "Max ~16 mA per pin (a few mA is safer) - use a resistor for LEDs, "
               "a transistor/relay driver for anything bigger.")

COLORS = {"3v3": "#f0a030", "5v": "#d03030", "gnd": "#303030", "id": "#8060b0"}
POLL_MS = 100


def _default_confirm(title, msg):
    return messagebox.askyesno(title, msg, icon="warning")


class App:
    def __init__(self, root: tk.Misc, ctl: Controller, confirm=_default_confirm):
        self.root, self.ctl, self.confirm = root, ctl, confirm
        self.selected = None
        self.items = {}  # bcm -> canvas circle id
        root.title("Pi 5 GPIO Controller")
        self._build()
        self.select(None)
        self._poll()

    # ---------- layout
    def _build(self):
        r = self.root
        top = ttk.Frame(r, padding=6)
        top.pack(fill="x")
        ttk.Label(top, text="Raspberry Pi 5 GPIO", font=("TkDefaultFont", 14, "bold")).pack(side="left")
        self.all_off_btn = tk.Button(top, text="ALL OFF", bg="#c62828", fg="white",
                                     font=("TkDefaultFont", 11, "bold"), command=self.all_off)
        self.all_off_btn.pack(side="right")
        if self.ctl.backend.simulated:
            tk.Label(top, text=" SIMULATION - no real pins are being driven ", bg="#ffd54f").pack(
                side="right", padx=8)
        tk.Label(r, text=SAFETY_TEXT, bg="#fff3cd", wraplength=760, justify="left").pack(fill="x")

        body = ttk.Frame(r, padding=6)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, width=330, height=20 * 26 + 20, bg="white", highlightthickness=1)
        self.canvas.pack(side="left")
        self._draw_header()

        self.panel = ttk.Frame(body, padding=(12, 0))
        self.panel.pack(side="left", fill="both", expand=True)
        self.title = ttk.Label(self.panel, text="", font=("TkDefaultFont", 13, "bold"))
        self.title.pack(anchor="w")
        self.info = ttk.Label(self.panel, text="", wraplength=360, justify="left")
        self.info.pack(anchor="w", pady=(0, 6))

        mf = ttk.LabelFrame(self.panel, text="Mode", padding=4)
        mf.pack(fill="x")
        self.mode_var = tk.StringVar(value="unused")
        for text, val in (("Unused", "unused"), ("Input", "input"), ("Output", "output")):
            ttk.Radiobutton(mf, text=text, value=val, variable=self.mode_var,
                            command=self._mode_changed).pack(side="left", padx=4)

        self.toggle_btn = tk.Button(self.panel, text="OFF", width=14, height=2,
                                    font=("TkDefaultFont", 12, "bold"), command=self.toggle)
        self.toggle_btn.pack(pady=10, anchor="w")
        self.input_lbl = ttk.Label(self.panel, text="")
        self.input_lbl.pack(anchor="w")

        tf = ttk.LabelFrame(self.panel, text="Software timer (auto toggle)", padding=6)
        tf.pack(fill="x", pady=6)
        self.period_var = tk.StringVar(value="10")
        self.duty_var = tk.StringVar(value="50")
        ttk.Label(tf, text="Period (ms):").grid(row=0, column=0, sticky="w")
        ttk.Entry(tf, textvariable=self.period_var, width=10).grid(row=0, column=1)
        ttk.Label(tf, text="Duty ON (%):").grid(row=1, column=0, sticky="w")
        ttk.Entry(tf, textvariable=self.duty_var, width=10).grid(row=1, column=1)
        self.t_start = ttk.Button(tf, text="Start timer", command=self.start_timer)
        self.t_start.grid(row=0, column=2, padx=6)
        self.t_stop = ttk.Button(tf, text="Stop timer", command=self.stop_timer)
        self.t_stop.grid(row=1, column=2, padx=6)
        ttk.Label(tf, justify="left", wraplength=340, foreground="#555",
                  text="One full ON+OFF cycle = Period. 10 ms with 50% = 5 ms ON, 5 ms OFF. "
                       "Software timing has jitter (see below); min 1 ms.").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.stats_lbl = ttk.Label(tf, text="")
        self.stats_lbl.grid(row=3, column=0, columnspan=3, sticky="w")

        hf = ttk.LabelFrame(self.panel, text="Hardware PWM (accurate; GPIO 12/13/18/19)", padding=6)
        hf.pack(fill="x", pady=6)
        self.freq_var = tk.StringVar(value="100")
        self.hduty_var = tk.StringVar(value="50")
        ttk.Label(hf, text="Frequency (Hz):").grid(row=0, column=0, sticky="w")
        ttk.Entry(hf, textvariable=self.freq_var, width=10).grid(row=0, column=1)
        ttk.Label(hf, text="Duty ON (%):").grid(row=1, column=0, sticky="w")
        ttk.Entry(hf, textvariable=self.hduty_var, width=10).grid(row=1, column=1)
        self.h_start = ttk.Button(hf, text="Start HW PWM", command=self.start_hw)
        self.h_start.grid(row=0, column=2, padx=6)
        self.h_stop = ttk.Button(hf, text="Stop HW PWM", command=self.stop_timer)
        self.h_stop.grid(row=1, column=2, padx=6)
        self.h_note = ttk.Label(hf, wraplength=340, foreground="#555", justify="left")
        self.h_note.grid(row=2, column=0, columnspan=3, sticky="w")

        self.status = tk.StringVar(value="Click a GPIO pin on the diagram.")
        ttk.Label(r, textvariable=self.status, relief="sunken", anchor="w").pack(fill="x", side="bottom")

    def _draw_header(self):
        c = self.canvas
        for p in PINS:
            row = (p.physical - 1) // 2
            left = p.physical % 2 == 1
            y = 20 + row * 26
            x = 150 if left else 190
            fill = COLORS.get(p.kind, "#ccd6e0")
            cid = c.create_oval(x - 9, y - 9, x + 9, y + 9, fill=fill, outline="black", width=2)
            txt = f"{p.label}  ({p.physical})" if left else f"({p.physical})  {p.label}"
            if p.special:
                txt = f"{p.label}*  ({p.physical})" if left else f"({p.physical})  {p.label}*"
            c.create_text(x - 16 if left else x + 16, y, text=txt, anchor="e" if left else "w",
                          font=("TkDefaultFont", 9))
            for item in (cid,):
                c.tag_bind(item, "<Button-1>", lambda e, pin=p: self.on_pin_click(pin))
            if p.bcm is not None:
                self.items[p.bcm] = cid
        c.create_text(165, 20 + 20 * 26 - 4, text="* special function pin", font=("TkDefaultFont", 8))

    # ---------- selection
    def on_pin_click(self, pin):
        if pin.bcm is None:
            what = {"gnd": "Ground", "3v3": "3.3 V power", "5v": "5 V power"}.get(pin.kind, pin.special)
            self.status.set(f"Pin {pin.physical} ({pin.label}) is {what}; it cannot be controlled.")
            return
        self.select(pin.bcm)

    def select(self, bcm):
        self.selected = bcm
        for w in (self.toggle_btn,):
            pass
        self._refresh()

    # ---------- actions
    def _run(self, fn, *args, **kw):
        """Run a controller call, asking for confirmation on special pins."""
        try:
            return fn(*args, **kw)
        except SpecialPinError as e:
            if self.confirm("Special-function pin",
                            f"GPIO{e.bcm} is used for {e.function}.\n\nDriving it may break connected "
                            "hardware (e.g. a HAT, sensor, or serial console).\n\nContinue anyway?"):
                kw["allow_special"] = True
                return self._run_retry(fn, *args, **kw)
        except (BackendError, PwmError, ValueError) as e:
            self.status.set(f"Error: {e}")
            messagebox.showerror("GPIO error", str(e))
        return None

    def _run_retry(self, fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except (BackendError, PwmError, ValueError) as e:
            self.status.set(f"Error: {e}")
            messagebox.showerror("GPIO error", str(e))

    def _mode_changed(self):
        b = self.selected
        if b is None:
            return
        m = self.mode_var.get()
        if m == "output":
            self._run(self.ctl.set_output, b)
        elif m == "input":
            self._run(self.ctl.set_input, b)
        else:
            self._run(self.ctl.release, b)
        self._refresh()

    def toggle(self):
        b = self.selected
        if b is None:
            return
        st = self.ctl.state(b)
        if st.mode != "output":
            if self._run(self.ctl.set_output, b) is None and self.ctl.state(b).mode != "output":
                self._refresh()
                return
        if self.ctl.state(b).timer:
            self.status.set("Stop the timer first.")
            return
        self._run(self.ctl.toggle, b)
        self._refresh()

    def start_timer(self):
        b = self.selected
        if b is None:
            return
        try:
            period = float(self.period_var.get()) / 1000.0
            duty = float(self.duty_var.get()) / 100.0
        except ValueError:
            self.status.set("Period and duty must be numbers.")
            return
        self._run(self.ctl.start_timer, b, period, duty)
        self._refresh()

    def start_hw(self):
        b = self.selected
        if b is None:
            return
        try:
            freq = float(self.freq_var.get())
            duty = float(self.hduty_var.get()) / 100.0
        except ValueError:
            self.status.set("Frequency and duty must be numbers.")
            return
        self._run(self.ctl.start_hw_pwm, b, freq, duty)
        self._refresh()

    def stop_timer(self):
        if self.selected is not None:
            self.ctl.stop_timer(self.selected)
        self._refresh()

    def all_off(self):
        self.ctl.all_off()
        self.status.set("All outputs driven low and all timers stopped.")
        self._refresh()

    # ---------- refresh
    def _poll(self):
        self._refresh()
        self.root.after(POLL_MS, self._poll)

    def _refresh(self):
        for bcm, cid in self.items.items():
            st = self.ctl.state(bcm)
            if st.mode == "unused":
                fill = "#ccd6e0"
            else:
                if st.mode == "input":
                    try:
                        st.value = self.ctl.read(bcm)
                    except BackendError:
                        pass
                if st.timer:
                    fill = "#42a5f5"
                elif st.mode == "output":
                    fill = "#43a047" if st.value else "#1b5e20"
                else:
                    fill = "#ffee58" if st.value else "#9e9d24"
            self.canvas.itemconfig(cid, fill=fill, width=4 if bcm == self.selected else 2,
                                   outline="#e91e63" if bcm == self.selected else "black")
        b = self.selected
        enabled = b is not None
        for w in (self.toggle_btn,):
            w.config(state="normal" if enabled else "disabled")
        if not enabled:
            self.title.config(text="No pin selected")
            self.info.config(text="")
            return
        pin, st = BY_BCM[b], self.ctl.state(b)
        self.title.config(text=f"GPIO{b}  (physical pin {pin.physical})")
        self.info.config(text=f"Special function: {pin.special}" if pin.special else "General purpose pin")
        self.mode_var.set(st.mode)
        if st.mode == "output" and not st.timer:
            self.toggle_btn.config(text="ON" if st.value else "OFF",
                                   bg="#43a047" if st.value else "#9e9e9e", state="normal")
        elif st.timer:
            self.toggle_btn.config(text="TIMER RUNNING", bg="#42a5f5", state="disabled")
        else:
            self.toggle_btn.config(text="Click to enable (OFF)", bg="#9e9e9e", state="normal")
        self.input_lbl.config(text=f"Input level: {'HIGH' if st.value else 'LOW'}" if st.mode == "input" else "")
        stats = self.ctl.timer_stats(b)
        if st.timer == "software" and stats:
            err = f"  ERROR: {stats.error}" if stats.error else ""
            self.stats_lbl.config(text=f"Toggles: {stats.toggles}  late avg {stats.avg_late_us:.0f} us, "
                                       f"max {stats.max_late_us:.0f} us{err}")
        elif st.timer == "hardware":
            self.stats_lbl.config(text=f"Hardware PWM running at {1 / st.period_s:g} Hz")
        else:
            self.stats_lbl.config(text="")
        hw_ok = self.ctl.pwm is not None and self.ctl.pwm.available and HardwarePwm.supports(b)
        self.h_start.config(state="normal" if hw_ok else "disabled")
        if not HardwarePwm.supports(b):
            self.h_note.config(text="This pin has no hardware PWM.")
        elif not (self.ctl.pwm and self.ctl.pwm.available):
            self.h_note.config(text="Not available: enable the pwm overlay in config.txt and reboot "
                                    "(see README).")
        else:
            self.h_note.config(text="Uses the RP1 PWM block; timing is done in hardware.")


def run(ctl: Controller):
    root = tk.Tk()
    app = App(root, ctl)

    def on_close():
        ctl.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    try:
        root.mainloop()
    finally:
        ctl.close()
    return app
