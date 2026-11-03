"""Everything that touches the SDP: always through Docker, never installed on the host."""
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys

HOST_DIR = pathlib.Path(__file__).parent / "host"
ENV_SH = "$HOME/qnx800/qnxsdp-env.sh"
# The SDP volume is mounted read-only at /sdp. qcc takes a lock in ~/.qnx/license, so the license files
# are copied into the throwaway container home: the SDP and its license on the volume are never written.
PREP = ("mkdir -p /root/.qnx && cp -a /sdp/.qnx/license /sdp/.qnx/qconfig /root/.qnx/ 2>/dev/null; "
        "ln -sfn /sdp/qnx800 /root/qnx800; ")
KEY_RE = re.compile(r"[A-Z0-9]{4}(-[A-Z0-9]{4}){4}")
PLATFORM_NOISE = "WARNING: The requested image's platform"


def volume():
    return os.environ.get("QNXRUN_VOLUME", "qnx-home")


def image():
    return os.environ.get("QNXRUN_IMAGE", "qnx-host")


def mask(line, secrets=()):
    for secret in secrets:
        if secret:
            line = line.replace(secret, "<masked>")
    return KEY_RE.sub("<license key>", line)


def _owner_args():
    """Linux bind mounts keep the container's root owner; hand generated files back to the user."""
    return ["-e", f"QNXRUN_OWNER={os.getuid()}:{os.getgid()}"] if sys.platform != "darwin" and os.getuid() else []


def docker_run(mounts, script, env=(), workdir=None, capture=False):
    """Run a bash script in the SDP container, with the SDP volume read-only."""
    cmd = ["docker", "run", "--rm", "-v", f"{volume()}:/sdp:ro"]
    for host, target in mounts:
        cmd += ["-v", f"{host}:{target}"]
    for item in env:
        cmd += ["-e", item]
    if workdir:
        cmd += ["-w", workdir]
    cmd += _owner_args() + [image(), "bash", "-c", PREP + script]
    p = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE if capture else None, text=True)
    err = "".join(l for l in p.stderr.splitlines(True) if not l.startswith(PLATFORM_NOISE))
    sys.stderr.write(mask(err)) if not capture else None
    return p.returncode, (p.stdout or "") + err if capture else ""


def in_sdp(*argv):
    """A script that loads the SDP environment and then runs argv literally."""
    chown = 'rc=$?; [ -z "${QNXRUN_OWNER:-}" ] || find /work -user 0 -exec chown "$QNXRUN_OWNER" {} +; exit $rc'
    return f'source "{ENV_SH}" >/dev/null; {shlex.join(argv)}; {chown}'


def build(args, make=False):
    """`qcc -Vgcc_ntox86_64 args` or `make args` with the current directory mounted at /work."""
    argv = (["make"] if make else ["qcc", "-Vgcc_ntox86_64"]) + list(args)
    return docker_run([(os.getcwd(), "/work")], in_sdp(*argv), workdir="/work")[0]


def compile_c(source, out):
    """Compile one .c for `run`/`test`, with the output in the qnxrun cache."""
    source, out = pathlib.Path(source).resolve(), pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = ["qcc", "-Vgcc_ntox86_64", "-std=gnu11", "-Wall", "-O2", source.name, "-o", f"/out/{out.name}"]
    return docker_run([(source.parent, "/work"), (out.parent, "/out")], in_sdp(*argv), workdir="/work")[0]


def build_image(vm_dir):
    """Bake the agent into a QNX image; the SDP volume stays read-only."""
    return docker_run([(vm_dir, "/vm"), (HOST_DIR, "/qnxrun:ro")], "bash /qnxrun/build-image.sh")[0]


def image_exists():
    return subprocess.run(["docker", "image", "inspect", image()], capture_output=True).returncode == 0


def build_docker_image():
    return subprocess.run(["docker", "build", "-t", image(), str(HOST_DIR)]).returncode


def sdp_present():
    return docker_run([], f'test -f "{ENV_SH}"', capture=True)[0] == 0


def install(installer):
    """Headless SDP install into the volume. Refuses a volume that already has an SDP: licenses are precious."""
    env_names = ("QNX_USER", "QNX_PASS", "QNX_KEY")
    missing = [n for n in env_names if not os.environ.get(n)]
    if missing:
        print(f"qnxrun: set {', '.join(missing)} in the environment; credentials are never taken as arguments",
              file=sys.stderr)
        return 2
    installer = pathlib.Path(installer).resolve()
    if not installer.is_file():
        print(f"qnxrun: installer not found: {installer}", file=sys.stderr)
        return 2
    if not image_exists() and build_docker_image() != 0:
        return 1
    if sdp_present():
        print(f"qnxrun: volume {volume()} already has an SDP; nothing to do. "
              "Use QNXRUN_VOLUME=<other> for a separate install.", file=sys.stderr)
        return 0
    # -e NAME without a value: docker takes it from our environment and it never appears in argv.
    cmd = ["docker", "run", "--rm", "-v", f"{volume()}:/root", "-v", f"{installer.parent}:/inst:ro",
           "-v", f"{HOST_DIR}:/qnxrun:ro", "-e", f"QSC_RUN=/inst/{installer.name}"]
    for name in env_names:
        cmd += ["-e", name]
    cmd += [image(), "bash", "/qnxrun/install-sdp.sh"]
    secrets = [os.environ[n] for n in env_names]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    for line in p.stdout:
        sys.stdout.write(mask(line, secrets))
    return p.wait()


def doctor():
    """Each check prints ok or the exact command that fixes it. Returns the number of failures."""
    results = []

    def check(name, ok, fix):
        results.append(ok)
        print(f"{'ok  ' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      fix: {fix}"))
        return ok

    has_docker = shutil.which("docker") is not None
    daemon = has_docker and subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    check("docker installed", has_docker, "install Docker: https://docs.docker.com/get-docker/")
    check("docker daemon running", daemon, "start Docker Desktop, or: sudo systemctl start docker")
    has_image = daemon and image_exists()
    check(f"docker image {image()}", has_image, f"docker build -t {image()} {shlex.quote(str(HOST_DIR))}")
    vol = daemon and subprocess.run(["docker", "volume", "inspect", volume()], capture_output=True).returncode == 0
    install_fix = "QNX_USER=... QNX_PASS=... QNX_KEY=... qnxrun install --installer qnx-setup-*.run"
    check(f"docker volume {volume()}", vol, install_fix)
    present = has_image and vol and sdp_present()
    check("QNX SDP 8.0 in the volume", present, install_fix)
    license_ok = False
    if present:
        probe = "echo 'int main(void){return 0;}' > /tmp/p.c && qcc -Vgcc_ntox86_64 /tmp/p.c -o /tmp/p"
        rc, output = docker_run([], f'source "{ENV_SH}" >/dev/null; {probe}', capture=True)
        license_ok = rc == 0
        if not license_ok:
            print(mask(output).rstrip())
    check("QNX license accepted by qcc", license_ok, install_fix + "  (qnxrun never activates twice on its own)")
    qemu = shutil.which("qemu-system-x86_64") is not None
    check("qemu-system-x86_64", qemu, "brew install qemu  (macOS), sudo apt-get install qemu-system-x86  (Linux)")
    return results.count(False)
