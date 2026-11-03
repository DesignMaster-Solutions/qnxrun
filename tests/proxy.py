"""A man in the middle on the serial socket: damages traffic the way a bad link would. Used by unit and e2e tests."""
import re
import socket
import threading

B64 = rb"[A-Za-z0-9+/]"


class SerialProxy:
    """`mode`: payload (one base64 byte toward the target), output (one in an O frame), exit (the exit code)."""

    def __init__(self, upstream, listen, mode):
        self.upstream, self.mode, self.done, self.in_put = upstream, mode, False, False
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(listen)
        self.listener.listen(1)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        client, _ = self.listener.accept()
        server = socket.socket(socket.AF_UNIX)
        server.connect(self.upstream)
        threading.Thread(target=self._pump, args=(client, server, self._to_target), daemon=True).start()
        self._pump(server, client, self._to_host)

    @staticmethod
    def _pump(src, dst, change):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(change(data))
        except OSError:
            pass
        for s in (src, dst):
            s.close()

    @staticmethod
    def _flip(match):
        char = match.group(0)
        return b"B" if char == b"A" else b"A"

    def _to_target(self, data):
        if self.mode != "payload" or self.done:
            return data
        if re.search(rb"(^|\n)P \d+ ", data):
            self.in_put = True
            head, sep, rest = data.partition(b"\n")
            return head + sep + self._damage(rest)
        return self._damage(data) if self.in_put else data

    def _damage(self, data):
        m = re.search(B64, data)
        if not m:
            return data
        self.done = True
        return data[: m.start()] + self._flip(m) + data[m.end():]

    def _to_host(self, data):
        if self.done:
            return data
        pattern = {"output": rb"(@[0-9a-f]+ O )(" + B64 + rb")", "exit": rb"(@[0-9a-f]+ X exit )(\d)"}.get(self.mode)
        if not pattern:
            return data
        m = re.search(pattern, data)
        if not m:
            return data
        self.done = True
        new = (b"9" if m.group(2) != b"9" else b"1") if self.mode == "exit" else self._flip(re.match(B64, m.group(2)))
        return data[: m.start(2)] + new + data[m.end(2):]

    def close(self):
        self.listener.close()
