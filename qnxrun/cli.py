"""qnxrun command line."""
import argparse
import os
import pathlib
import select
import shutil
import sys
import tempfile
import termios
import tty
import xml.etree.ElementTree as ET

from . import __version__, sdp, target
from .console import EX_INFRA, Refused, TargetError

DETACH = b"\x1d"  # Ctrl-]


def say(message):
    print(f"qnxrun: {message}", file=sys.stderr, flush=True)


def write_to(stream):
    def write(data):
        try:
            stream.buffer.write(data)
            stream.buffer.flush()
        except BrokenPipeError:
            pass
    return write


def describe(result, timeout):
    """Explain on stderr every result that is not simply the program's own exit code."""
    if result.kind == "timeout":
        say(f"--timeout {timeout:g} expired; process killed on the target (rc {result.rc})")
    elif result.kind == "sig":
        say(f"process killed by signal {result.value} on the target (rc {result.rc})")
    elif result.kind == "exec":
        say(f"target could not execute it: {os.strerror(result.value)}; is it a QNX x86_64 binary? (rc {result.rc})")
    elif result.kind == "abort":
        say("interrupted; process killed on the target")


def resolve_binary(path, workdir):
    """A .c goes through qcc into the cache; anything else is sent as is."""
    if not os.path.isfile(path):
        raise TargetError(f"no such file: {path}")
    if not path.endswith(".c"):
        return path
    out = pathlib.Path(workdir) / pathlib.Path(path).stem
    if sdp.compile_c(path, out) != 0:
        raise TargetError(f"qcc failed for {path}")
    return str(out)


def attached_serial(instance):
    """QNXRUN_SERIAL points at another console socket, such as a test proxy in front of QEMU."""
    override = os.environ.get("QNXRUN_SERIAL")
    return target.ConsoleLock(instance, override) if override else instance.session()


def ensure_up():
    instance = target.default_instance()
    if not instance.running() and not os.environ.get("QNXRUN_SERIAL"):
        say("starting the QNX target; it stays up for the next runs (`qnxrun down` stops it)")
        instance.start(target.ensure_image())
    return instance


def cmd_doctor(_args):
    failures = sdp.doctor()
    print("all good" if not failures else f"{failures} check(s) failed")
    return 1 if failures else 0


def cmd_install(args):
    return sdp.install(args.installer)


def cmd_build(args):
    if not args.args and not args.make:
        say("nothing to build: qnxrun build hello.c -o hello, or qnxrun build --make")
        return 2
    return sdp.build(args.args, make=args.make)


def cmd_up(_args):
    instance = target.default_instance()
    if instance.running():
        print("already up")
        return 0
    instance.start(target.ensure_image())
    print("up")
    return 0


def cmd_down(_args):
    target.default_instance().stop()
    print("down")
    return 0


def cmd_status(_args):
    instance = target.default_instance()
    if instance.running():
        print(f"up  pid={instance.pid()}  serial={instance.sock}  console={instance.console}")
        return 0
    print("down")
    return 1


def interactive(serial):
    say("attached to the QNX console; Ctrl-] detaches")
    serial.send(b"\r")
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    tty.setraw(fd)
    try:
        while True:
            ready = select.select([serial.sock, fd], [], [])[0]
            if serial.sock in ready:
                data = serial.sock.recv(4096)
                if not data:
                    raise TargetError("serial link closed")
                os.write(sys.stdout.fileno(), data)
            if fd in ready:
                data = os.read(fd, 1024)
                if not data or DETACH in data:
                    return 0
                serial.send(data)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def cmd_shell(args):
    instance = ensure_up()
    if args.command is None and not sys.stdin.isatty():
        say("qnxrun shell needs a terminal; use qnxrun shell -c '<command>' from scripts")
        return 2
    with attached_serial(instance) as serial:
        serial.sync()
        if args.command is None:
            return interactive(serial)
        serial.start_agent()
        result = serial.run(["sh", "-c", args.command], args.timeout, write_to(sys.stdout), write_to(sys.stderr))
        if result.kind != "abort":
            serial.stop_agent()
        describe(result, args.timeout)
        return result.rc


def cmd_run(args):
    with tempfile.TemporaryDirectory(dir=target.cache_root()) as work:
        binary = resolve_binary(args.program, work)
        instance = ensure_up()
        with attached_serial(instance) as serial:
            serial.sync()
            serial.start_agent()
            result = serial.run_binary(binary, args.args, args.timeout, write_to(sys.stdout), write_to(sys.stderr))
            if result.kind != "abort":
                serial.stop_agent()
    describe(result, args.timeout)
    return result.rc


def clean_xml(text):
    """Drop what XML 1.0 cannot carry (most control characters), keep everything else."""
    return "".join(c for c in text if c in "\t\n\r" or " " <= c <= "\ud7ff" or "\ue000" <= c <= "\ufffd" or c > "\uffff")


def write_junit(path, cases):
    # No time attribute, on purpose: qnxrun publishes no timing of QNX.
    suite = ET.Element("testsuite", name="qnxrun", tests=str(len(cases)),
                       failures=str(sum(c["status"] == "failure" for c in cases)),
                       errors=str(sum(c["status"] == "error" for c in cases)))
    for c in cases:
        case = ET.SubElement(suite, "testcase", classname="qnxrun", name=c["name"])
        if c["status"] != "passed":
            ET.SubElement(case, c["status"], message=c["message"])
        ET.SubElement(case, "system-out").text = clean_xml(c["output"])
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def run_case(serial, binary, timeout):
    captured = bytearray()

    def tee(stream):
        forward = write_to(stream)

        def write(data):
            captured.extend(data)
            forward(data)
        return write

    case = {"name": os.path.basename(binary)}
    try:
        serial.sync()
        serial.start_agent()
        result = serial.run_binary(binary, [], timeout, tee(sys.stdout), tee(sys.stderr))
        serial.stop_agent()
        ok = result.rc == 0
        case.update(status="passed" if ok else "failure", message=f"{result.kind} {result.value} (rc {result.rc})")
        rc = result.rc
    except TargetError as e:
        status = "error"
        case.update(status=status, message=str(e))
        rc = e.rc
        say(f"{case['name']}: {e}")
    case["output"] = bytes(captured).decode("utf-8", "replace")
    print(f"{'PASS' if case['status'] == 'passed' else 'FAIL'} {case['name']} rc={rc}", flush=True)
    return case


def cmd_test(args):
    console_path = args.console or "qnxrun-console.log"
    instance = target.test_instance()
    cases = []
    try:
        with tempfile.TemporaryDirectory(dir=target.cache_root()) as work:
            binaries = [resolve_binary(b, work) for b in args.binaries]
            say("clean boot of the QNX target")
            instance.start(target.ensure_image())
            with instance.session() as serial:
                cases = [run_case(serial, b, args.timeout) for b in binaries]
    finally:
        instance.stop()
        if instance.console.exists():
            shutil.copyfile(instance.console, console_path)
            say(f"console saved to {console_path}")
        if args.junit:
            write_junit(args.junit, cases)
        shutil.rmtree(instance.dir, ignore_errors=True)
    failed = [c for c in cases if c["status"] != "passed"]
    print(f"{len(cases) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


def parser():
    top = argparse.ArgumentParser(prog="qnxrun", description="Compile, deliver and run on QNX 8.0 in QEMU.")
    top.add_argument("--version", action="version", version=__version__)
    sub = top.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="check Docker, QEMU, the SDP volume and the license").set_defaults(fn=cmd_doctor)
    p = sub.add_parser("install", help="headless SDP install into a Docker volume (QNX_USER, QNX_PASS, QNX_KEY)")
    p.add_argument("--installer", required=True, help="qnx-setup-*.run from myQNX")
    p.set_defaults(fn=cmd_install)
    p = sub.add_parser("build", help="qcc -Vgcc_ntox86_64 ARGS in the container, current directory mounted")
    p.add_argument("--make", action="store_true", help="run make ARGS instead of qcc")
    p.add_argument("args", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_build)
    sub.add_parser("up", help="start the persistent target").set_defaults(fn=cmd_up)
    sub.add_parser("down", help="stop it").set_defaults(fn=cmd_down)
    sub.add_parser("status", help="say whether it is up").set_defaults(fn=cmd_status)
    p = sub.add_parser("shell", help="console of the target, or -c COMMAND with its exit code")
    p.add_argument("-c", dest="command")
    p.add_argument("--timeout", type=float)
    p.set_defaults(fn=cmd_shell)
    p = sub.add_parser("run", help="send a binary or .c to the target, stream it, return its exit code")
    p.add_argument("--timeout", type=float, help="seconds; kill on the target and exit 124")
    p.add_argument("program")
    p.add_argument("args", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("test", help="CI mode: clean boot, run each binary, JUnit XML and console artifact")
    p.add_argument("binaries", nargs="+")
    p.add_argument("--junit", help="write JUnit XML here")
    p.add_argument("--console", help="console artifact path (default qnxrun-console.log)")
    p.add_argument("--timeout", type=float, default=300, help="seconds per binary (default 300)")
    p.set_defaults(fn=cmd_test)
    return top


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["build"] and argv[1:2] not in (["-h"], ["--help"]):
        # Everything after `build` belongs to qcc or make, flags included: no argparse in between.
        make = argv[1:2] == ["--make"]
        args = argparse.Namespace(fn=cmd_build, make=make, args=argv[2 if make else 1:])
    else:
        args = parser().parse_args(argv)
    try:
        return args.fn(args)
    except Refused as e:
        say(f"REFUSED: {e}; nothing was executed")
        return e.rc
    except TargetError as e:
        say(str(e))
        return e.rc
    except KeyboardInterrupt:
        return 130
    except FileNotFoundError as e:
        say(f"{e.filename or e}: not found; run `qnxrun doctor`")
        return EX_INFRA
