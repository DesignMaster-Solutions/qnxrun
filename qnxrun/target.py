"""The QEMU target: an image in the per-user cache and instances of it, always -snapshot."""
import fcntl
import hashlib
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from . import sdp
from .console import Serial, TargetError

BOOT_MARK = b"Startup complete"
BOOT_TIMEOUT = 1800  # a limit for a stuck boot on a loaded host, not an expectation


def cache_root():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    root = pathlib.Path(base) / "qnxrun"
    root.mkdir(parents=True, exist_ok=True)
    return root


def image_key():
    h = hashlib.sha256()
    for name in ("qnxrun-agent.c", "build-image.sh"):
        h.update((sdp.HOST_DIR / name).read_bytes())
    return h.hexdigest()[:16]


def ensure_image():
    """The image for this qnxrun version, built once. A failed build leaves nothing behind."""
    final = cache_root() / "images" / image_key()
    if (final / "output" / "ifs.bin").exists():
        return final
    final.parent.mkdir(exist_ok=True)
    with open(final.parent / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (final / "output" / "ifs.bin").exists():
            return final
        print("qnxrun: building the QNX image with mkqnximage (first use only)", file=sys.stderr)
        work = pathlib.Path(tempfile.mkdtemp(prefix="build-", dir=final.parent))
        try:
            if sdp.build_image(work) != 0:
                raise TargetError("mkqnximage failed; run `qnxrun doctor`")
            work.rename(final)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return final


class Instance:
    def __init__(self, directory):
        self.dir = pathlib.Path(directory)
        self.sock = self.dir / "serial.sock"
        self.pidfile = self.dir / "qemu.pid"
        self.readyfile = self.dir / "ready"
        self.console = self.dir / "console.log"

    def pid(self):
        """The QEMU pid, only if that pid is still our QEMU."""
        try:
            pid = int(self.pidfile.read_text())
        except (OSError, ValueError):
            return None
        p = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
        return pid if str(self.sock) in p.stdout else None

    def running(self):
        return self.pid() is not None and self.readyfile.exists()

    def start(self, image_dir):
        """Boot a fresh -snapshot QEMU, no network, serial on a Unix socket, and wait for the shell."""
        self.dir.mkdir(parents=True, exist_ok=True)
        if len(str(self.sock)) > 100:
            raise TargetError(f"path too long for a Unix socket: {self.sock}; set XDG_CACHE_HOME to a shorter path")
        self.stop()
        out = pathlib.Path(image_dir) / "output"
        # Same flags as mkqnximage's qemu/runimage for x86_64, minus the bridge network. wait=on: nothing
        # of the boot console is lost, QEMU starts when the connection below is made.
        # shortcut: TCG only, proven everywhere we run; add -accel kvm when a Linux host proves it.
        cmd = ["qemu-system-x86_64", "-snapshot", "-smp", "2", "-cpu", "max", "-m", "1G", "-no-reboot",
               "-display", "none", "-monitor", "none", "-nic", "none",
               "-drive", f"file={out}/disk-qemu.vmdk,if=ide,id=drv0", "-kernel", f"{out}/ifs.bin",
               "-serial", f"unix:{self.sock},server=on,wait=on",
               "-object", "rng-random,filename=/dev/urandom,id=rng0", "-device", "virtio-rng-pci,rng=rng0"]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=open(self.dir / "qemu.err", "w"), start_new_session=True,
                                    env={**os.environ, "TMPDIR": str(self.dir)})
        except OSError as e:
            raise TargetError(f"cannot start QEMU: {e}; run `qnxrun doctor`") from e
        self.pidfile.write_text(str(proc.pid))
        try:
            with open(self.console, "wb") as log:
                serial = Serial(str(self.sock), log)
                try:
                    serial.wait_for(BOOT_MARK, BOOT_TIMEOUT, "the QNX boot")
                    serial.sync()
                finally:
                    serial.close()
            self.readyfile.write_text("ready\n")
        except TargetError as e:
            err = (self.dir / "qemu.err").read_text(errors="replace").strip()
            self.stop()
            raise TargetError(f"{e}{'; QEMU said: ' + err if err else ''}") from e

    def stop(self):
        pid = self.pid()
        if pid:
            os.kill(pid, signal.SIGTERM)
            for _ in range(100):
                if self.pid() is None:
                    break
                time.sleep(0.1)
            else:
                os.kill(pid, signal.SIGKILL)
        for f in (self.pidfile, self.readyfile, self.sock):
            f.unlink(missing_ok=True)

    def session(self):
        """Exclusive use of the serial console: one host command at a time."""
        return ConsoleLock(self)


class ConsoleLock:
    def __init__(self, instance, path=None):
        self.instance, self.path = instance, path or str(instance.sock)

    def __enter__(self):
        self.lock = open(self.instance.dir / "lock", "w")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("qnxrun: waiting for another qnxrun using this target", file=sys.stderr)
            fcntl.flock(self.lock, fcntl.LOCK_EX)
        self.log = open(self.instance.console, "ab")
        self.serial = Serial(self.path, self.log)
        return self.serial

    def __exit__(self, *exc):
        self.serial.close()
        self.log.close()
        self.lock.close()


def default_instance():
    return Instance(cache_root() / "target")


def test_instance():
    return Instance(tempfile.mkdtemp(prefix="t-", dir=cache_root()))
