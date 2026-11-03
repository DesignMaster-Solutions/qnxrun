#!/usr/bin/env python3
"""Stands in for qemu-system-x86_64: the real agent, compiled natively, behind a shell on a pty."""
import os
import pty
import select
import signal
import socket
import sys


def main():
    arg = sys.argv[sys.argv.index("-serial") + 1]
    path = arg.split("unix:")[1].split(",")[0]
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(path)
    listener.listen(1)
    pid, master = pty.fork()
    if pid == 0:
        os.environ["PS1"] = "# "
        if os.path.exists("/bin/bash"):  # /bin/sh is dash on Ubuntu, which knows no --norc
            os.execv("/bin/bash", ["bash", "--norc", "--noprofile", "-i"])
        os.execv("/bin/sh", ["sh", "-i"])
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    # Like QNX, announce the boot only once the shell is up: a ^C into a shell that is still starting kills it.
    early = b""
    while b"# " not in early:
        early += os.read(master, 65536)
    conn = None
    announce = True
    while True:
        fds = [master, listener] + ([conn] if conn else [])
        for fd in select.select(fds, [], [])[0]:
            if fd is listener:
                conn = listener.accept()[0]
                if announce and os.environ.get("FAKE_QEMU_NO_BOOT") is None:
                    conn.sendall(b"fake boot\nStartup complete\n" + early)
                    announce = False
            elif fd is master:
                data = os.read(master, 65536)
                if conn:
                    try:
                        conn.sendall(data)
                    except OSError:
                        conn = None
            elif fd is conn:
                try:
                    data = conn.recv(65536)
                except OSError:  # a client that closes with unread data resets; QEMU survives that too
                    data = b""
                if not data:
                    conn = None
                else:
                    os.write(master, data)


if __name__ == "__main__":
    main()
