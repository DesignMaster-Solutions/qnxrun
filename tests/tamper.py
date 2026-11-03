#!/usr/bin/env python3
"""Run a program through a serial proxy that damages one byte of the transfer. Expect rc 123.

  python3 -m tests.tamper <qnx binary> [args]
"""
import os
import pathlib
import subprocess
import sys
import tempfile

from tests.proxy import SerialProxy

cache = pathlib.Path(os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache")
listen = pathlib.Path(tempfile.gettempdir()) / f"qnxrun-tamper-{os.getpid()}.sock"
proxy = SerialProxy(str(cache / "qnxrun" / "target" / "serial.sock"), str(listen), "payload")
try:
    entry = pathlib.Path(__file__).resolve().parent.parent / "qnxrun.py"
    rc = subprocess.run([sys.executable, str(entry), "run", *sys.argv[1:]],
                        env={**os.environ, "QNXRUN_SERIAL": str(listen)}).returncode
finally:
    proxy.close()
    listen.unlink(missing_ok=True)
print(f"proxy damaged one byte: {proxy.done}")
sys.exit(rc)
