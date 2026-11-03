"""Shared harness: PATH with fake docker and qemu, a private cache, and the CLI as a subprocess."""
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
ENTRY = REPO / "qnxrun.py"
AGENT_SRC = REPO / "qnxrun" / "host" / "qnxrun-agent.c"


def script(path, body):
    path = pathlib.Path(path)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class Stubbed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Short, private root: Unix socket paths have a low length limit.
        cls.root = pathlib.Path(tempfile.mkdtemp(prefix="qr", dir="/tmp"))
        cls.bin = cls.root / "bin"
        cls.bin.mkdir()
        subprocess.run(["cc", "-std=gnu11", "-D_GNU_SOURCE", "-O1", "-Wall", "-Wextra", "-Werror", str(AGENT_SRC),
                        "-o", str(cls.bin / "qnxrun-agent")], check=True)
        script(cls.bin / "docker", f'#!/bin/sh\nexec "{sys.executable}" "{REPO}/tests/fake_docker.py" "$@"\n')
        script(cls.bin / "qemu-system-x86_64", f'#!/bin/sh\nexec "{sys.executable}" "{REPO}/tests/fake_qemu.py" "$@"\n')

    @classmethod
    def tearDownClass(cls):
        cls.qnxrun("down")
        shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def env(cls, **extra):
        return {**os.environ, "PATH": f"{cls.bin}:{os.environ['PATH']}", "XDG_CACHE_HOME": str(cls.root / "c"),
                "FAKE_ROOT": str(cls.root), **extra}

    @classmethod
    def qnxrun(cls, *args, env=None, timeout=120, cwd=None):
        return subprocess.run([sys.executable, str(ENTRY), *map(str, args)], env=cls.env(**(env or {})), cwd=cwd,
                              capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)

    @classmethod
    def popen(cls, *args, env=None):
        return subprocess.Popen([sys.executable, "-u", str(ENTRY), *map(str, args)], env=cls.env(**(env or {})),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)

    def prog(self, name, body):
        """A target program for the fake: a shell script, delivered by the same protocol as a binary."""
        return script(self.root / name, "#!/bin/sh\n" + body + "\n")

    def docker_log(self):
        path = self.root / "docker.log"
        return path.read_text() if path.exists() else ""
