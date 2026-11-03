"""End to end on a real QNX 8.0 QEMU target. Needs the SDP volume; run with QNXRUN_E2E=1.

  QNXRUN_E2E=1 python3 -m unittest tests.e2e.test_qnx -v
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import uuid
import xml.etree.ElementTree as ET

from tests.proxy import SerialProxy

HERE = pathlib.Path(__file__).resolve().parent
ENTRY = HERE.parent.parent / "qnxrun.py"
OUT = HERE / "build"


def qnxrun(*args, env=None, timeout=1800):
    return subprocess.run([sys.executable, str(ENTRY), *map(str, args)], capture_output=True, text=True,
                          timeout=timeout, env={**os.environ, **(env or {})}, cwd=HERE, stdin=subprocess.DEVNULL)


@unittest.skipUnless(os.environ.get("QNXRUN_E2E") == "1", "needs a QNX SDP; set QNXRUN_E2E=1")
class OnQNX(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        OUT.mkdir(exist_ok=True)
        for name in ("hello", "stream", "hang", "victim", "segv"):
            p = qnxrun("build", "-Wall", "-Werror", f"{name}.c", "-o", f"build/{name}")
            assert p.returncode == 0, p.stderr
        p = qnxrun("up")
        assert p.returncode == 0, p.stderr

    def test_exit_code_7_reaches_the_host(self):
        p = qnxrun("run", OUT / "hello", "7")
        self.assertEqual(p.returncode, 7, p.stderr)
        self.assertIn("hello from QNX 8.0.0", p.stdout)

    def test_args_arrive(self):
        args = ["0", "a b", "$(reboot)", "'q'", 'd"q', "*", ""]
        p = qnxrun("run", OUT / "hello", *args)
        self.assertEqual(p.returncode, 0, p.stderr)
        expected = "".join(f"arg[{i}]=[{a}]\n" for i, a in enumerate(args, 1))
        self.assertTrue(p.stdout.endswith(expected), p.stdout)

    def test_stdout_streams_before_the_end(self):
        p = subprocess.Popen([sys.executable, "-u", str(ENTRY), "run", "--timeout", "120", str(OUT / "stream")],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=HERE)
        line = p.stdout.readline()
        still_running = p.poll() is None
        rc = p.wait(600)
        p.stdout.close()
        p.stderr.close()
        self.assertEqual(line, "first line, still running\n")
        self.assertTrue(still_running, "the line must reach the host while the program runs")
        self.assertEqual(rc, 124)

    def test_timeout_kills_the_hung_program_on_the_target(self):
        p = qnxrun("run", "--timeout", "5", OUT / "hang")
        self.assertEqual(p.returncode, 124, p.stderr)
        self.assertIn("process killed on the target", p.stderr)
        left = qnxrun("shell", "-c", "pidin -f a | grep -c hang")
        self.assertEqual(left.stdout.strip(), "0", "no process of the run may survive: " + left.stdout)

    def test_signal_is_128_plus_n(self):
        self.assertEqual(qnxrun("run", OUT / "segv").returncode, 139)

    def test_source_file_is_compiled_then_run(self):
        self.assertEqual(qnxrun("run", HERE / "victim.c").returncode, 3)

    def test_host_binary_is_not_executed(self):
        p = qnxrun("run", "/bin/ls")
        self.assertEqual(p.returncode, 126, p.stderr)

    def test_adulterated_bytes_are_refused(self):
        qnxrun("shell", "-c", "rm -f /tmp/qnxrun-victim-ran")
        upstream = pathlib.Path(os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache")
        upstream = upstream / "qnxrun" / "target" / "serial.sock"
        listen = pathlib.Path(tempfile.gettempdir()) / f"qp{uuid.uuid4().hex[:6]}.sock"
        proxy = SerialProxy(str(upstream), str(listen), "payload")
        try:
            p = qnxrun("run", OUT / "victim", env={"QNXRUN_SERIAL": str(listen)})
        finally:
            proxy.close()
            listen.unlink(missing_ok=True)
        self.assertTrue(proxy.done, "the proxy must have damaged the transfer")
        self.assertEqual(p.returncode, 123, p.stderr)
        self.assertIn("REFUSED", p.stderr)
        self.assertNotIn("victim ran", p.stdout)
        ran = qnxrun("shell", "-c", "test -e /tmp/qnxrun-victim-ran")
        self.assertEqual(ran.returncode, 1, "the damaged binary must not have executed")
        left = qnxrun("shell", "-c", "ls /tmp/qnxrun | grep -c victim")
        self.assertEqual(left.stdout.strip(), "0", "nothing of a refused transfer stays on the target")
        # The same file over an intact link runs: the refusal came from the damage.
        self.assertEqual(qnxrun("run", OUT / "victim").returncode, 3)

    def test_ci_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            junit, console = pathlib.Path(tmp) / "junit.xml", pathlib.Path(tmp) / "console.log"
            p = qnxrun("test", OUT / "hello", OUT / "victim", "--junit", junit, "--console", console)
            self.assertEqual(p.returncode, 1, p.stderr)
            suite = ET.parse(junit).getroot()
            self.assertEqual((suite.get("tests"), suite.get("failures")), ("2", "1"))
            self.assertIn("rc 3", suite.find("testcase[@name='victim']/failure").get("message"))
            self.assertIn(b"Startup complete", console.read_bytes())
            self.assertNotIn(b"time=", junit.read_bytes())
