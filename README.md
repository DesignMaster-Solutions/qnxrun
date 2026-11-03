# qnxrun

**`cargo run` for QNX 8.0.** One command compiles your program, delivers it to a QNX 8.0 target in QEMU, streams its output and hands its exit code back to your shell, from a Mac or a Linux x86_64 machine.

```console
$ qnxrun run hello.c 7 "a b"
hello from QNX 8.0.0 x86pc
arg[1]=[7]
arg[2]=[a b]
$ echo $?
7
```

## The problem

The QNX SDP is a Linux/Windows x86_64 toolchain. [QNX's own release notes](https://www.qnx.com/developers/docs/relnotes8.0/com.qnx.doc.release_notes/topic/sdp8_rn.html) say it "isn't supported on macOS anymore" and "isn't supported on host systems that use ARM processors". Even on a supported Linux host, getting from "I wrote code" to "it ran on the target and I have the exit code" means building an image, booting QEMU, setting up a network and ssh, or opening the IDE. We found no public GitHub Action or test runner for QNX 8.0, only setup containers.

qnxrun closes that loop:

- the SDP runs in a Docker container on a volume, **read-only**: qnxrun never writes to your SDP or your license;
- the target is a QNX 8.0 image made with `mkqnximage`, with a small agent compiled by `qcc` baked in, cached per user and booted in QEMU with `-snapshot`;
- the only link is the **serial console** over a Unix socket: no network, no ssh, no keys;
- every binary is verified by SHA-256 **on the target** before it may execute, and every result is hash-checked on the host before qnxrun trusts it.

## Three commands

```sh
qnxrun doctor                 # Docker, QEMU, SDP volume, license: each failure prints the command that fixes it
qnxrun run hello.c 7 "a b"    # compile with qcc, boot the target once, run, stream, exit with its code
qnxrun test build/t_* --junit junit.xml   # CI mode: clean boot, every binary, JUnit XML, console artifact
```

![qnxrun demo](docs/demo.gif)

## Install

Requirements: Python 3.8+ (standard library only), Docker, `qemu-system-x86_64`, and your own licensed QNX SDP 8.0.

```sh
pipx install git+https://github.com/DesignMaster-Solutions/qnxrun   # or: git clone ... && ./qnxrun.py
qnxrun doctor
```

No SDP yet? qnxrun installs it headless into a Docker volume. Credentials come only from the environment, never from arguments, and are masked in everything qnxrun prints:

```sh
export QNX_USER=you@example.com QNX_PASS=... QNX_KEY=XXXX-XXXX-XXXX-XXXX-XXXX
qnxrun install --installer ~/Downloads/qnx-setup-2.0.4-linux.run
```

`install` refuses to touch a volume that already holds an SDP, so it cannot re-activate a license by accident. Defaults: volume `qnx-home`, image `qnx-host`; override with `QNXRUN_VOLUME` and `QNXRUN_IMAGE`.

## Commands

| Command | What it does |
|---|---|
| `qnxrun doctor` | Checks Docker, the `qnx-host` image, the SDP volume, the license (a real `qcc` compile) and QEMU. |
| `qnxrun install --installer F` | Headless SDP install into the volume; credentials from `QNX_USER`, `QNX_PASS`, `QNX_KEY`. |
| `qnxrun build ARGS` | `qcc -Vgcc_ntox86_64 ARGS` in the container with the current directory mounted. `build --make ARGS` runs `make`. |
| `qnxrun up` / `down` / `status` | Persistent target. `run` starts it on first use and leaves it up. |
| `qnxrun shell` | The target's console (Ctrl-] detaches). `shell -c CMD` runs CMD and returns its exit code. |
| `qnxrun run [--timeout S] PROG [ARGS]` | PROG is a QNX binary or a `.c`. Arguments arrive byte for byte; no shell on the way. |
| `qnxrun test BIN... [--junit F] [--console F] [--timeout S]` | Fresh boot, runs each binary, writes JUnit XML and the console log, exit 1 if any failed. |

### Exit codes

| rc | Meaning |
|---|---|
| 0–255 | The program's own exit code |
| 128+N | The program was killed by signal N on the target |
| 123 | Transfer refused: the bytes on the target did not match the hash; nothing was executed |
| 124 | `--timeout` expired; the process and everything it spawned were killed on the target |
| 125 | qnxrun, QEMU or the serial link failed, or a result arrived damaged and was not trusted |
| 126 | The target could not execute the file (not a QNX x86_64 program) |
| 130 | Interrupted with Ctrl-C; the process was killed on the target |

A program may itself exit with 123–130; qnxrun then prints nothing extra on stderr, while every qnxrun-generated code comes with a `qnxrun:` line.

## How it works

`qnxrun-agent` (one C file, `qnxrun/host/qnxrun-agent.c`) is compiled with `qcc` into the image. qnxrun types `qnxrun-agent <nonce>` at the console shell; the agent switches the tty to raw and speaks a line protocol:

1. **Transfer.** `P <size> <sha256> <path>`, then base64 lines, each acknowledged before the next (stop and wait, so the serial input buffer cannot overflow). The file is written as `.part` with mode 0600; only if the SHA-256 computed **on the target** matches does it become `0755` and get its final name. Otherwise it is deleted and the host gets `V bad`: rc 123.
2. **Run.** `R <timeout_ms> <argc>` with each argument base64-encoded, so no shell ever parses them. The child gets a **pty** for stdout (its stdio stays line-buffered, so output really streams), a pipe for stderr, `/dev/null` for stdin and its own session. On timeout the agent kills the whole process group with `SIGKILL` and reaps it.
3. **Result.** All output travels inside `@<nonce>` frames as base64, so a program cannot forge a result by printing one. The final `X` frame carries a SHA-256 over every output byte and the result itself; the host recomputes it and refuses a damaged exit code (rc 125).

A new session first sends `^C`, which makes a stale agent kill its child and leave, then proves the shell is responsive with an echo of a fresh token. One host command uses the console at a time (a file lock).

## What has been proven, and how

Everything below was run, and every check was first seen failing. Results and the exact commands are in [docs/verification.md](docs/verification.md).

- **Stub suite, no SDP needed** (this repository's CI on Ubuntu and macOS): `python -m unittest discover -s tests -t .`. Docker and QEMU are replaced by stubs, but the **real agent** is compiled natively and driven through a real pty, so the protocol, hashing, timeout, signals and streaming are the production code. A man-in-the-middle proxy (`tests/proxy.py`) damages the serial traffic.
- **End to end on QNX 8.0.0 x86_64 in QEMU** (`QNXRUN_E2E=1 python3 -m unittest tests.e2e.test_qnx -v`): a `qcc`-built program exits 7 and the host receives 7; arguments with spaces, quotes and `$(...)` arrive literally; a line reaches the host while the program is still running; a program that ignores `SIGTERM` and forks is killed by `--timeout` and no process of it remains; `SIGSEGV` gives 139; a host binary gives 126; a transfer damaged by the proxy is refused, never executes and leaves nothing behind; `qnxrun test` writes JUnit with the failure recorded.
- **Mutants** (copies outside the repository): exit code always 0, target hash not compared, timeout ignored. Each one is caught by the suites; see the rc table in docs/verification.md.
- **`qnxrun install`** is tested against a stub `qnxsoftwarecenter_clt` that prints the credentials in clear: none reach the output or the Docker command line. A real SDP install through qnxrun has **not** been run (we hold one Named User license, already installed); the same `qnxsoftwarecenter_clt` steps were proven by the installer script of our [Cold-Chain Guard](https://github.com/DesignMaster-Solutions/coldchain-guard-qnx) project.

## Limits

- x86_64 target only, in QEMU with TCG. No hardware target, no aarch64, no KVM/HVF acceleration yet.
- The transport is a serial console: fine for test binaries, slow for large files. One command at a time per target.
- No stdin to the program, no environment variables or working directory from the host (cwd is `/tmp/qnxrun`). Arguments are limited to 768 bytes each.
- `build` mounts the current directory; sources outside it are not visible. On Linux, files created by the container are handed back to your user; that path is tested only with stubs.
- A program that leaves its session with `setsid()` escapes the timeout kill.
- If the agent itself dies while the target stays up, the host only notices after `--timeout` plus a grace period; without `--timeout` it waits. QEMU dying is detected at once.
- **CI**: `examples/github-actions-self-hosted.yml` is an example for a self-hosted runner that already has an SDP volume. It is **not proven** on any runner. Never put the SDP or a license in a cache or a log of a hosted runner.
- qnxrun publishes no timing: it prints no boot or run durations, and the JUnit XML carries no `time` attribute. That is deliberate; the QNX SDP non-commercial license does not allow publishing benchmarks.

## License

Apache-2.0, by Design Master Solutions, LLC. QNX, the SDP and the target image are not distributed; you need your own license from [qnx.com/getqnx](https://www.qnx.com/getqnx).
