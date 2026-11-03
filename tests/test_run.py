"""`run`, `shell -c` and the lifecycle, against the real agent compiled natively."""
import hashlib
import os
import signal
import subprocess
import time
import uuid

from .proxy import SerialProxy
from .support import Stubbed


def alive(marker):
    return subprocess.run(["pgrep", "-f", marker], capture_output=True).returncode == 0


class RunTests(Stubbed):
    def test_exit_code_reaches_the_host(self):
        for code in (0, 7, 42, 255):
            p = self.qnxrun("run", self.prog("c.sh", f"exit {code}"))
            self.assertEqual(p.returncode, code, p.stderr)

    def test_autostarts_and_status(self):
        self.qnxrun("down")
        self.assertEqual(self.qnxrun("status").returncode, 1)
        p = self.qnxrun("run", self.prog("a.sh", "echo hi"))
        self.assertEqual((p.returncode, p.stdout), (0, "hi\n"), p.stderr)
        self.assertIn("starting the QNX target", p.stderr)
        self.assertEqual(self.qnxrun("status").returncode, 0)
        self.assertIn("already up", self.qnxrun("up").stdout)

    def test_image_is_built_once(self):
        self.qnxrun("run", self.prog("a.sh", "true"))
        self.qnxrun("down")
        self.qnxrun("run", self.prog("a.sh", "true"))
        self.assertEqual(self.docker_log().count("build-image.sh"), 1)
        self.assertRegex(self.docker_log(), r"qnx-home:/sdp:ro .*build-image.sh")

    def test_args_arrive_literally(self):
        args = ["a b", "$(touch /tmp/pwned)", "'q'", 'd"q', "*", ""]
        p = self.qnxrun("run", self.prog("args.sh", 'for a in "$@"; do echo "[$a]"; done'), *args)
        self.assertEqual(p.stdout, "".join(f"[{a}]\n" for a in args), p.stderr)

    def test_flags_after_the_program_belong_to_it(self):
        p = self.qnxrun("run", self.prog("args.sh", 'echo "$@"'), "--timeout", "1")
        self.assertEqual(p.stdout, "--timeout 1\n")

    def test_argument_too_long_is_refused(self):
        p = self.qnxrun("run", self.prog("a.sh", "true"), "x" * 2000)
        self.assertEqual(p.returncode, 125)
        self.assertIn("too long", p.stderr)

    def test_stdout_and_stderr_stay_apart(self):
        self.qnxrun("up")
        p = self.qnxrun("run", self.prog("o.sh", "echo out; echo err >&2"))
        self.assertEqual((p.stdout, p.stderr), ("out\n", "err\n"))

    def test_binary_safe_output(self):
        p = subprocess.run(["python3", "-c", ""], capture_output=True)
        self.assertEqual(p.returncode, 0)
        q = self.qnxrun("run", self.prog("b.sh", r"printf 'caf\303\251\n'"))
        self.assertEqual(q.stdout, "café\n")

    def test_output_streams_while_running(self):
        marker = uuid.uuid4().hex
        p = self.popen("run", "--timeout", "4", self.prog("s.sh", f'echo first; sleep 3; : {marker}; echo last'))
        self.assertEqual(p.stdout.readline(), "first\n")
        self.assertIsNone(p.poll(), "the line must arrive before the program ends")
        self.assertEqual(p.stdout.readline(), "last\n")
        self.assertEqual(p.wait(30), 0)
        p.stderr.close()
        p.stdout.close()

    def test_output_cannot_forge_the_result(self):
        p = self.qnxrun("run", self.prog("f.sh", "echo '@0123456789abcdef X exit 0 abc'; echo 'X exit 0'; exit 3"))
        self.assertEqual(p.returncode, 3)

    def test_timeout_kills_on_target_with_own_rc(self):
        marker = f"hang{uuid.uuid4().hex}"
        prog = self.prog(f"{marker}.sh", "trap '' TERM INT\n(while :; do sleep 1; done) &\nwhile :; do sleep 1; done")
        p = self.qnxrun("run", "--timeout", "1", prog)
        self.assertEqual(p.returncode, 124, p.stderr)
        self.assertIn("--timeout 1 expired", p.stderr)
        self.assertFalse(alive(marker), "the process and its children must be gone")

    def test_signal_death_is_128_plus_n(self):
        p = self.qnxrun("run", self.prog("k.sh", "kill -SEGV $$"))
        self.assertEqual(p.returncode, 128 + signal.SIGSEGV, p.stderr)

    def test_not_a_program_is_126(self):
        garbage = self.root / "garbage"
        garbage.write_bytes(b"\x00\x01not a program\n")
        p = self.qnxrun("run", garbage)
        self.assertEqual(p.returncode, 126, p.stderr)

    def test_text_that_sh_would_run_is_still_126(self):
        text = self.root / "plain.txt"
        text.write_text("echo i-was-run-by-sh\n")
        text.chmod(0o755)
        p = self.qnxrun("run", text)
        self.assertEqual(p.returncode, 126, p.stdout + p.stderr)
        self.assertNotIn("i-was-run-by-sh", p.stdout)

    def test_missing_and_empty_files(self):
        self.assertEqual(self.qnxrun("run", self.root / "nope").returncode, 125)
        empty = self.root / "empty"
        empty.write_bytes(b"")
        self.assertEqual(self.qnxrun("run", empty).returncode, 123)

    def test_transfer_sizes_around_the_chunk(self):
        for size in (383, 384, 385, 767, 768, 769, 50000):
            body = "#" + "x" * size + "\nexit 0"
            prog = self.prog(f"size{size}.sh", body)
            p = self.qnxrun("run", prog)
            self.assertEqual(p.returncode, 0, f"{size}: {p.stderr}")

    def test_agent_sha256_matches_hashlib(self):
        prog = self.prog("h.sh", "#" + "y" * 1000 + "\nexit 0")
        self.assertEqual(len(hashlib.sha256(prog.read_bytes()).hexdigest()), 64)
        # Any difference between the C and Python digests would be a refusal.
        self.assertEqual(self.qnxrun("run", prog).returncode, 0)

    def test_interrupted_host_kills_the_program(self):
        marker = f"int{uuid.uuid4().hex}"
        p = self.popen("run", self.prog(f"{marker}.sh", "echo up; while :; do sleep 1; done"))
        self.assertEqual(p.stdout.readline(), "up\n")
        p.send_signal(signal.SIGINT)
        self.assertEqual(p.wait(30), 130)
        p.stdout.close()
        p.stderr.close()
        self.assertEqual(self.qnxrun("run", self.prog("n.sh", "exit 4")).returncode, 4)
        self.assertFalse(alive(marker))

    def test_frozen_agent_is_an_error_after_timeout_plus_grace(self):
        # README, Limits: if the agent stops answering, the host gives up after --timeout plus a grace period.
        marker = f"frz{uuid.uuid4().hex}"
        prog = self.prog(f"{marker}.sh", "kill -STOP $PPID\nwhile :; do sleep 1; done")
        try:
            p = self.qnxrun("run", "--timeout", "1", prog, timeout=100)
        finally:
            for pid in subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split():
                agent = subprocess.run(["ps", "-o", "ppid=", "-p", pid], capture_output=True, text=True).stdout.strip()
                for victim in filter(None, (pid, agent)):
                    subprocess.run(["kill", "-9", victim])
        self.assertEqual(p.returncode, 125, p.stderr)
        self.assertIn("no answer from the target", p.stderr)
        self.assertEqual(self.qnxrun("run", self.prog("n.sh", "exit 6")).returncode, 6)

    def test_next_run_recovers_after_a_killed_host(self):
        p = self.popen("run", self.prog("kh.sh", "echo up; while :; do sleep 1; done"))
        self.assertEqual(p.stdout.readline(), "up\n")
        p.kill()
        p.wait(10)
        p.stdout.close()
        p.stderr.close()
        self.assertEqual(self.qnxrun("run", self.prog("n.sh", "exit 6")).returncode, 6)

    def test_shell_command_propagates(self):
        self.qnxrun("up")
        p = self.qnxrun("shell", "-c", "echo there; exit 9")
        self.assertEqual((p.returncode, p.stdout), (9, "there\n"), p.stderr)

    def test_shell_without_a_terminal_says_so(self):
        p = self.qnxrun("shell")
        self.assertEqual(p.returncode, 2)

    def test_down_stops_qemu_and_is_idempotent(self):
        self.qnxrun("up")
        self.assertEqual(self.qnxrun("down").returncode, 0)
        self.assertEqual(self.qnxrun("down").returncode, 0)
        self.assertEqual(self.qnxrun("status").returncode, 1)

    def test_stale_pid_is_not_killed(self):
        self.qnxrun("up")
        cache = self.root / "c" / "qnxrun" / "target"
        real = (cache / "qemu.pid").read_text()
        sleeper = subprocess.Popen(["sleep", "30"])
        (cache / "qemu.pid").write_text(str(sleeper.pid))
        self.qnxrun("down")
        self.assertIsNone(sleeper.poll(), "a reused pid must not be killed")
        sleeper.kill()
        sleeper.wait()
        os.kill(int(real), signal.SIGKILL)

    def test_boot_never_finishing_is_an_error_not_a_hang(self):
        self.qnxrun("down")
        # QEMU exits at once: the wait must end with rc 125 instead of pretending.
        (self.bin / "qemu-real").write_text((self.bin / "qemu-system-x86_64").read_text())
        (self.bin / "qemu-system-x86_64").write_text("#!/bin/sh\necho no such machine >&2; exit 1\n")
        try:
            p = self.qnxrun("up", timeout=120)
        finally:
            (self.bin / "qemu-system-x86_64").write_text((self.bin / "qemu-real").read_text())
        self.assertEqual(p.returncode, 125, p.stderr)


class DamagedLink(Stubbed):
    def through_proxy(self, mode, *args):
        self.qnxrun("up")
        upstream = self.root / "c" / "qnxrun" / "target" / "serial.sock"
        listen = self.root / f"p{uuid.uuid4().hex[:6]}.sock"
        proxy = SerialProxy(str(upstream), str(listen), mode)
        try:
            return self.qnxrun(*args, env={"QNXRUN_SERIAL": str(listen)}), proxy
        finally:
            proxy.close()

    def test_adulterated_binary_is_refused_and_never_runs(self):
        ran = self.root / f"ran{uuid.uuid4().hex}"
        prog = self.prog("victim.sh", f"touch {ran}\n#" + "A" * 2000)
        p, proxy = self.through_proxy("payload", "run", prog)
        self.assertTrue(proxy.done, "the proxy must have damaged something: " + p.stderr)
        self.assertEqual(p.returncode, 123, p.stderr)
        self.assertIn("REFUSED", p.stderr)
        self.assertFalse(ran.exists(), "a refused binary must not execute")
        leftovers = [f for f in os.listdir("/tmp/qnxrun") if "victim" in f]
        self.assertEqual(leftovers, [], "a refused transfer leaves nothing on the target")

    def test_same_program_runs_without_the_damage(self):
        prog = self.prog("victim2.sh", "exit 0\n#" + "A" * 2000)
        self.assertEqual(self.qnxrun("run", prog).returncode, 0)

    def test_damaged_exit_code_is_not_trusted(self):
        p, proxy = self.through_proxy("exit", "run", self.prog("e.sh", "exit 3"))
        self.assertTrue(proxy.done, p.stderr)
        self.assertEqual(p.returncode, 125, p.stderr)
        self.assertIn("exit code not trusted", p.stderr)

    def test_damaged_output_is_not_trusted(self):
        p, proxy = self.through_proxy("output", "run", self.prog("o.sh", "echo AAAA; exit 0"))
        self.assertTrue(proxy.done)
        self.assertEqual(p.returncode, 125, p.stderr)
