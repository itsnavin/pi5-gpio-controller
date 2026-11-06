import pytest

tk = pytest.importorskip("tkinter")

from pi5gpio.backends import MockBackend  # noqa: E402
from pi5gpio.controller import Controller  # noqa: E402
from pi5gpio.gui import App  # noqa: E402


@pytest.fixture
def app():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    answers = []
    ctl = Controller(MockBackend())
    a = App(root, ctl, confirm=lambda t, m: (answers.append(m), a.allow)[1])
    a.allow = True
    a.answers = answers
    yield a
    ctl.close()
    root.destroy()


def test_toggle_and_timer_via_gui(app):
    app.select(17)
    app.toggle()  # click-to-enable
    assert app.ctl.state(17).mode == "output" and app.ctl.state(17).value is True
    app.toggle()
    assert app.ctl.state(17).value is False
    app.period_var.set("10")
    app.start_timer()
    assert app.ctl.state(17).timer == "software"
    app.stop_timer()
    assert app.ctl.state(17).timer is None
    app.all_off()


def test_special_pin_confirmation(app):
    app.select(2)
    app.allow = False
    app.toggle()
    assert app.answers and app.ctl.state(2).mode == "unused"
    app.allow = True
    app.toggle()
    assert app.ctl.state(2).mode == "output"
