#!/usr/bin/env python3
"""Stands in for docker: logs its argv and imitates just enough of the SDP container."""
import os
import pathlib
import re
import subprocess
import sys

argv = sys.argv[1:]
root = pathlib.Path(os.environ["FAKE_ROOT"])
with open(root / "docker.log", "a") as log:
    log.write(" ".join(argv) + "\n")
failing = os.environ.get("FAKE_DOCKER_FAIL", "").split(",")

if argv[:1] in (["info"], ["build"]):
    sys.exit(1 if argv[0] in failing else 0)
if argv[:2] == ["image", "inspect"]:
    sys.exit(1 if "image" in failing else 0)
if argv[:2] == ["volume", "inspect"]:
    sys.exit(1 if "volume" in failing else 0)
assert argv[0] == "run", argv

mounts, env, workdir, i = {}, {}, None, 2
while argv[i].startswith("-"):
    flag, value = argv[i], argv[i + 1]
    i += 2
    if flag == "-v":
        host, target = value.split(":")[:2]
        mounts[target] = host
    elif flag == "-e":
        name, _, val = value.partition("=")
        env[name] = val if "=" in value else os.environ[name]
    elif flag == "-w":
        workdir = value
command = argv[i + 1:]  # after the image name
if command[:2] == ["bash", "-c"]:
    assert "/sdp/.qnx/license" in command[2] and "qnx-home:/sdp:ro" in argv, argv  # SDP never writable
    line = command[2]
else:
    line = " ".join(command)
print("WARNING: The requested image's platform (linux/amd64) does not match", file=sys.stderr)


def local(path):
    for target, host in mounts.items():
        if path == target or path.startswith(target + "/"):
            return host + path[len(target):]
    return path


if "build-image.sh" in line:
    if "mkqnximage" in failing:
        sys.exit(1)
    out = pathlib.Path(mounts["/vm"]) / "output"
    out.mkdir(parents=True)
    (out / "ifs.bin").write_text("fake")
    (out / "disk-qemu.vmdk").write_text("fake")
    sys.exit(0)
if "install-sdp.sh" in line:
    home = root / "volume-home"
    home.mkdir(exist_ok=True)
    full = {**os.environ, **env, "HOME": str(home), "QSC_RUN": local(env["QSC_RUN"])}
    sys.exit(subprocess.run(["bash", local("/qnxrun/install-sdp.sh")], env=full).returncode)
if "test -f" in line:
    sys.exit(1 if "sdp" in failing else 0)
if "qcc" in line:
    if "license" in failing:
        print("license check failed for key ABCD-EFGH-IJKL-MNOP-QRST", file=sys.stderr)
        sys.exit(1)
    if "-Vgcc_ntox86_64" not in line:
        sys.exit(3)
    m = re.search(r"-o (\S+?)(;|\s|$)", line)
    source = re.search(r"(\S+\.c)\b", line)
    if source and os.path.exists(local(os.path.join(workdir or "/", source.group(1)))) and "#error" in pathlib.Path(local(os.path.join(workdir or "/", source.group(1)))).read_text():
        print("fake qcc: #error", file=sys.stderr)
        sys.exit(1)
    if m:
        out = pathlib.Path(local(os.path.join(workdir or "/", m.group(1))))
        out.write_text("#!/bin/sh\necho compiled-by-fake-qcc\nexit 5\n")
        out.chmod(0o755)
    sys.exit(0)
if "make" in line:
    sys.exit(0)
sys.exit(0)
