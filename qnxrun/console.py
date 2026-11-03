"""Host side of the serial protocol described at the top of host/qnxrun-agent.c."""
import base64
import hashlib
import os
import re
import secrets
import select
import socket
import time

EX_REFUSED = 123  # transfer refused: the bytes on the target did not match the hash
EX_TIMEOUT = 124  # --timeout expired and the process was killed on the target
EX_INFRA = 125  # qnxrun, QEMU, or the serial link failed
EX_EXEC = 126  # the target could not execute the file

CHUNK = 384  # raw bytes per line: 512 base64 chars, acked one by one so the tty input buffer cannot overflow
MAX_ARG_LINE = 1024
TIMEOUT_GRACE = 60  # beyond --timeout, the host stops trusting the agent


class TargetError(Exception):
    rc = EX_INFRA


class Refused(TargetError):
    rc = EX_REFUSED


class Result:
    def __init__(self, kind, value):
        self.kind, self.value = kind, value

    @property
    def rc(self):
        return {"exit": self.value, "sig": 128 + self.value, "timeout": EX_TIMEOUT,
                "abort": 130, "exec": EX_EXEC}[self.kind]


class Serial:
    def __init__(self, path, log=None, connect_timeout=30):
        self.log, self.buf, self.nonce = log, b"", None
        self.sock = socket.socket(socket.AF_UNIX)
        end = time.monotonic() + connect_timeout
        while True:
            try:
                self.sock.connect(path)
                return
            except OSError as e:
                if time.monotonic() > end:
                    raise TargetError(f"cannot connect to the serial socket {path}: {e}") from e
                time.sleep(0.2)

    def close(self):
        self.sock.close()

    def send(self, data):
        try:
            self.sock.sendall(data)
        except OSError as e:
            raise TargetError(f"serial link lost: {e}") from e

    def _fill(self, deadline, what):
        wait = None if deadline is None else max(0, deadline - time.monotonic())
        if not select.select([self.sock], [], [], wait)[0]:
            raise TargetError(f"no answer from the target while waiting for {what}")
        try:
            data = self.sock.recv(65536)
        except OSError as e:
            raise TargetError(f"serial link lost: {e}") from e
        if not data:
            raise TargetError(f"serial link closed while waiting for {what}")
        self.buf += data

    def _logged(self, data):
        if self.log and data:
            self.log.write(data)
            self.log.flush()

    def wait_for(self, needle, timeout, what, echo=None):
        """Consume console output up to and including `needle`."""
        end = time.monotonic() + timeout
        while needle not in self.buf:
            cut = max(0, len(self.buf) - len(needle))
            self._logged(self.buf[:cut])
            if echo:
                echo(self.buf[:cut])
            self.buf = self.buf[cut:]
            self._fill(end, what)
        head, self.buf = self.buf.split(needle, 1)
        self._logged(head + needle)
        if echo:
            echo(head + needle)

    def sync(self, timeout=60):
        """Reach a known shell prompt, whatever the previous session left behind."""
        last = None
        for _ in range(3):
            # ^C leaves a stale agent (killing its child) or clears a half-typed line. It may also flush
            # input arriving right after it, so a lost probe is retried with a new token.
            self.send(b"\x03\r")
            time.sleep(0.3)
            token = secrets.token_hex(8).encode()
            # The quotes make the echoed command line different from its output.
            self.send(b'echo ' + token + b'"OK"\r')
            try:
                self.wait_for(token + b"OK", timeout / 3, "the shell prompt")
                return
            except TargetError as e:
                last = e
        raise last

    # ---- agent session ----
    def _frame(self, deadline, what):
        pattern = re.compile(b"@" + self.nonce + rb" (\S) ?([^\r\n]*)\r?\n")
        while True:
            m = pattern.search(self.buf)
            if m:
                self._logged(self.buf[: m.start()].strip(b"\r\n"))
                self.buf = self.buf[m.end():]
                return m.group(1), m.group(2)
            self._fill(deadline, what)

    def start_agent(self):
        self.nonce = secrets.token_hex(8).encode()
        self.send(b"qnxrun-agent " + self.nonce + b"\r")
        kind, text = self._frame(time.monotonic() + 30, "the qnxrun agent")
        if kind != b"H" or text != b"1":
            raise TargetError(f"unexpected agent hello: {kind!r} {text!r}")

    def stop_agent(self):
        self.send(b"Q\n")
        self.nonce = None

    def put(self, data, remote):
        digest = hashlib.sha256(data).hexdigest()
        self.send(f"P {len(data)} {digest} {remote}\n".encode())
        for i in range(0, len(data), CHUNK):
            self.send(base64.b64encode(data[i:i + CHUNK]) + b"\n")
            kind, text = self._frame(time.monotonic() + 120, "the transfer")
            if kind == b"V":
                if text == b"ok" and i + CHUNK >= len(data):
                    return
                raise Refused(f"transfer refused by the target: {text.decode('latin-1')}")
            if kind != b"A":
                raise TargetError(f"unexpected frame during the transfer: {kind!r}")
        raise TargetError("the target acknowledged more data than was sent")

    def unlink(self, remote):
        self.send(f"U {remote}\n".encode())
        self._frame(time.monotonic() + 30, "the cleanup")

    def run(self, argv, timeout, on_out, on_err):
        lines = [base64.b64encode(a.encode()) for a in argv]
        if any(len(line) > MAX_ARG_LINE for line in lines):
            raise TargetError(f"argument too long for the serial protocol (limit {MAX_ARG_LINE * 3 // 4} bytes)")
        ms = int(timeout * 1000) if timeout else 0
        self.send(f"R {ms} {len(argv)}\n".encode() + b"".join(line + b"\n" for line in lines))
        deadline = time.monotonic() + timeout + TIMEOUT_GRACE if timeout else None
        sha = hashlib.sha256()
        try:
            while True:
                kind, text = self._frame(deadline, "the program on the target")
                if kind in (b"O", b"E"):
                    try:
                        data = base64.b64decode(text, validate=True)
                    except ValueError as e:
                        raise TargetError("corrupted output frame from the target") from e
                    sha.update(kind + data)
                    self._logged(data)
                    (on_out if kind == b"O" else on_err)(data)
                elif kind == b"X":
                    return self._result(text, sha)
                else:
                    raise TargetError(f"unexpected frame from the agent: {kind!r} {text!r}")
        except KeyboardInterrupt:
            self.send(b"\x03")  # the agent kills the child and leaves; the next session syncs anyway
            self.nonce = None
            return Result("abort", 0)

    @staticmethod
    def _result(text, sha):
        try:
            kind, value, digest = text.decode().split()
            sha.update(f"{kind} {value}".encode())
            if sha.hexdigest() != digest:
                raise ValueError("hash")
            return Result(kind, int(value))
        except ValueError as e:
            raise TargetError("corrupted result from the target: exit code not trusted") from e

    def run_binary(self, path, args, timeout, on_out, on_err):
        with open(path, "rb") as f:
            data = f.read()
        if not data:
            raise Refused(f"{path} is empty")
        name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(path))
        remote = f"/tmp/qnxrun/{secrets.token_hex(4)}-{name}"
        self.put(data, remote)
        result = self.run([remote] + list(args), timeout, on_out, on_err)
        if self.nonce and result.kind != "abort":
            self.unlink(remote)
        return result
