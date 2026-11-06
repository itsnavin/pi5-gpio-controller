"""Build dist/pi5-gpio-controller-<version>.tar.gz (stdlib only).

The tarball contains the source, install.sh, the desktop launcher, README and LICENSE.
Install on the Pi with:  tar xzf <file> && cd pi5-gpio-controller-<version> && ./install.sh
"""
import os
import re
import tarfile

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(root, "src", "pi5gpio", "__init__.py")) as f:
    version = re.search(r'__version__ = "(.+?)"', f.read()).group(1)
prefix = f"pi5-gpio-controller-{version}"
include = ["src", "packaging", "install.sh", "pyproject.toml", "README.md", "LICENSE", "tests"]
for script in ("install.sh", os.path.join("packaging", "pi5-gpio.desktop")):
    with open(os.path.join(root, script), "rb") as f:
        assert b"\r" not in f.read(), f"{script} must use LF line endings"
os.makedirs(os.path.join(root, "dist"), exist_ok=True)
out = os.path.join(root, "dist", prefix + ".tar.gz")


def filt(ti):
    if "__pycache__" in ti.name or ti.name.endswith(".pyc") or ".egg-info" in ti.name:
        return None
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    if ti.name.endswith("install.sh"):
        ti.mode = 0o755
    return ti


with tarfile.open(out, "w:gz") as tar:
    for item in include:
        tar.add(os.path.join(root, item), arcname=f"{prefix}/{item}", filter=filt)
print(out)
