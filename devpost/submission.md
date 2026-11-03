# qnxrun: Devpost form text

Prepared for Design Master Solutions, LLC. Form copy for the authorized Representative; not submitted.

Field coverage follows the public [event requirements](https://qnx.devpost.com/) and [official rules](https://qnx.devpost.com/rules), checked November 2, 2026. Exact labels, optional fields and character limits are checked in the authenticated form before submitting. This is our second entry; the rules allow more than one submission when they are substantially different. Our other entry, Cold-Chain Guard, competes in Robotics & Medical and shares no submitted code with this one. Editorial notes and the evidence table are not form copy.

## Project name

qnxrun

## Tagline

`cargo run` for QNX 8.0: one command compiles, delivers and runs your program on a QNX target in QEMU, streams its output and returns its exit code to your shell.

## Organization

Design Master Solutions, LLC

## Category

Developer Tooling & Host Tools

## Inspiration

We felt this problem ourselves while building for this hackathon. Our developers work on Macs, and [QNX's release notes](https://www.qnx.com/developers/docs/relnotes8.0/com.qnx.doc.release_notes/topic/sdp8_rn.html) state that QNX SDP "isn't supported on macOS anymore" and "isn't supported on host systems that use ARM processors". Even on a supported Linux host, getting from "I wrote code" to "it ran on the target and I have the exit code" meant building an image, booting QEMU, then setting up a network and ssh or opening the IDE, every time. On November 2, 2026 we searched GitHub for a GitHub Action or a test runner for QNX 8.0 and found only setup containers. A missing inner loop is the first thing a newcomer to QNX hits, and the missing CI runner is the first thing a team hits. We wanted the experience Rust developers take for granted with `cargo run`.

## What it does

`qnxrun run hello.c 7` compiles the file with the SDP's `qcc`, starts a QNX 8.0 target in QEMU if one is not already up, sends the binary over the serial console, runs it with its arguments, streams stdout and stderr to the host terminal as they are produced, and exits with the program's own exit code. A program killed by a signal returns 128 plus the signal number, as a shell would.

`--timeout` kills the process and everything it spawned on the target and returns 124. A binary whose bytes do not match on arrival is refused with 123 and never executes. A result damaged in transit is not trusted: 125.

`qnxrun test` is the CI mode: a clean boot, every binary in turn, JUnit XML, the console log as an artifact, and a non-zero exit code if anything failed.

`qnxrun doctor` checks Docker, QEMU, the SDP volume and the license with a real `qcc` compile, and every failure prints the exact command that fixes it. `qnxrun install` performs a headless SDP install into a Docker volume with credentials taken only from the environment and masked in all output. `qnxrun build` runs `qcc -Vgcc_ntox86_64` or `make` in the container with the current directory mounted. `qnxrun up`, `down`, `status` and `shell` manage a persistent target and its console; `shell -c` runs a target command and returns its exit code.

qnxrun is Python 3 using only the standard library, plus one C file for the target. It runs on macOS and Linux x86_64.

## How we built it

The SDP runs in a Linux x86_64 Docker container on a named volume, mounted read-only. `qcc` takes a lock in the license directory, so qnxrun copies the license files into the throwaway container home; the SDP and the license on the volume are never written.

The target image is built once per qnxrun version with `mkqnximage --type=qemu --arch=x86_64`, and our agent, `qnxrun-agent.c` compiled with `qcc`, is added through a `system_files.custom` snippet. The image is cached per user, and every boot uses QEMU `-snapshot`, no network and the serial console on a Unix socket. There is no ssh and there are no keys.

The agent turns the console into a small framed protocol. Transfers are base64 lines acknowledged one at a time, so the serial input buffer cannot overflow. The file is written with mode 0600 and becomes executable only if a SHA-256 computed on QNX matches the host's. Arguments are base64 too, so no shell parses them. The child gets a QNX pty from `posix_openpt` for stdout, so its stdio stays line-buffered and output really streams. It also gets a pipe for stderr and its own session, so `--timeout` can `SIGKILL` the whole process group and reap it. All output travels base64-encoded inside frames tagged with a per-session nonce, so a program cannot forge a result by printing one. The final frame carries a SHA-256 over every output byte and the result, which the host recomputes before it trusts the exit code.

## Challenges we ran into

Streaming was the first trap. Through a pipe, a C program's `printf` is fully buffered and nothing appears until it exits. We give the child a pty instead, and our end-to-end test runs a program that prints one line and never exits. The line has to reach the host while the program is still running.

A serial console is also a shared, stateful channel. A previous session may have left an agent running or a half-typed line. Each session first sends Ctrl-C, which makes a stale agent kill its child and exit, and then proves the shell is listening by echoing a fresh token. The token is quoted so the echoed command line can never match its output.

Our first license check failed because `qcc` needs to write a lock file and we had mounted the SDP read-only. Making the volume writable was the easy fix, but it would have let a tool touch someone's Named User license. We copy the few license files into the container instead.

Testing without an SDP was the last challenge. Our continuous integration cannot have QNX. We compile the same agent natively and run it behind a real pty in a QEMU stand-in, and a man-in-the-middle proxy damages the traffic. The protocol, hashing, timeout and signal paths under test are the production code. The real QNX suite then repeats the essential cases on the target.

## Accomplishments that we're proud of

On QNX 8.0.0 x86_64 in QEMU, a program built by `qcc` exits 7 and the host shell receives 7. Arguments containing spaces, quotes and `$(...)` arrive byte for byte. A line reaches the host while the program is still running. A program that ignores `SIGTERM` and forks is killed by `--timeout`, and no process of it remains on the target. A binary corrupted in transit by a proxy is refused, never runs and leaves nothing behind.

Every check was seen failing before it passed. Three deliberate mutants were each caught; we recorded the exit codes in `docs/verification.md`: an exit code forced to 0, the target-side hash comparison removed, and the timeout ignored.

## What we learned

A test runner is only as trustworthy as its least trusted byte. Running the program was the easy part. Most of the design went into not believing results we cannot verify: a hash checked where the code will execute, a nonce the program cannot see in its own output, and a hash over the result itself. We also learned to respect the SDP license as part of the product: the tool publishes no timing at all, and it never writes to the SDP.

## What's next for qnxrun

We want to prove a GitHub Actions workflow on a self-hosted runner that already holds an SDP, then add an aarch64 target, KVM acceleration on Linux hosts, stdin and environment forwarding, and a faster transfer for large binaries. Running on real hardware over a serial cable is the natural next target, because the protocol needs nothing but a console.

## Built with

Python 3 (standard library only), C11, QNX SDP 8.0, QNX OS 8.0.0, qcc, mkqnximage, QEMU x86_64, Docker, POSIX ptys (posix_openpt), SHA-256, base64, JUnit XML, GitHub Actions, asciinema.

## Public source code URL

https://github.com/DesignMaster-Solutions/qnxrun

## Project / additional link

https://github.com/DesignMaster-Solutions/qnxrun#three-commands

## Build and run instructions

Target: QNX OS 8.0.0, x86_64 in QEMU (TCG, `-snapshot`, no network, serial console on a Unix socket). Toolchain: QNX SDP 8.0, `qcc -Vgcc_ntox86_64`, `mkqnximage`, running in a Linux x86_64 Docker container (`qnxrun/host/Dockerfile`, Ubuntu 24.04). Host: macOS or Linux x86_64 with Python 3.8 or later, Docker and `qemu-system-x86_64`, and the user's own licensed SDP. No SDP files or target images are distributed.

```sh
git clone https://github.com/DesignMaster-Solutions/qnxrun && cd qnxrun
./qnxrun.py doctor                       # prints the fix for anything missing, including `qnxrun install`
./qnxrun.py run tests/e2e/hello.c 7 "a b"; echo $?   # 7
python3 -m unittest discover -s tests -t .           # stub suite, no SDP needed
QNXRUN_E2E=1 python3 -m unittest tests.e2e.test_qnx -v   # on QNX
```

The first run builds the image with `mkqnximage` and boots the target; later runs reuse it.

## Evidence of QNX use

- `qnxrun/host/qnxrun-agent.c`: runs on QNX 8.0. It uses `posix_openpt`, `grantpt`, `unlockpt` and `ptsname` (QNX `devc-pty`), `fork`, `setsid`, `execv`, `kill(-pgid, SIGKILL)`, `waitpid`, `CLOCK_MONOTONIC` and raw termios on the QNX serial console (`devc-ser8250`). It also contains a SHA-256 that checks transfers on the target.
- `qnxrun/host/build-image.sh`: `qcc -Vgcc_ntox86_64 -D_QNX_SOURCE`, and `mkqnximage --type=qemu --arch=x86_64 --ssh-ident=none` with a `local/snippets/system_files.custom` entry that puts the agent in the image.
- `qnxrun/target.py`: boots the mkqnximage output (`ifs.bin`, `disk-qemu.vmdk`) with the x86_64 QEMU flags of mkqnximage's own runimage, minus the network.
- `qnxrun/sdp.py`: sources `qnxsdp-env.sh`, compiles with `qcc` and probes the license with a real compile.
- `tests/e2e/test_qnx.py` with `hello.c`, `stream.c`, `hang.c`, `victim.c` and `segv.c`: acceptance on the QNX target.

## New or existing project / work during the submission period

New project created on November 2, 2026. The repository history records all of it. Host scripts were adapted from our own Cold-Chain Guard and codelab repositories, created on the same day: the Dockerfile and the headless install steps.

## Demo video URL

(to be filled in by the Representative)

## Representative, eligibility and final submission (not form copy)

The authorized Representative must complete account-specific identity and team fields, confirm eligibility and accept the rules on behalf of Design Master Solutions, LLC. No personal identity or consent is supplied by this document. Check any additional fields in the authenticated form, paste the video URL and submit before December 15, 2026, 2:00 pm EST.

## Editorial evidence map (not a form field)

| Claim | Repository evidence |
|---|---|
| Exit code, args, streaming, timeout, signals on QNX | `tests/e2e/test_qnx.py`, `docs/verification.md` |
| Hash checked on the target, refusal of damaged bytes | `cmd_put` in `qnxrun/host/qnxrun-agent.c`; `tests/proxy.py`; e2e `test_adulterated_bytes_are_refused` |
| Result integrity, no forged results | `cmd_run`/`frame` in the agent; `Serial._result` in `qnxrun/console.py`; `tests/test_run.py` |
| Read-only SDP, masked credentials | `PREP` and `install` in `qnxrun/sdp.py`; `tests/test_host.py` |
| Mutants caught | `docs/verification.md` |
| No published timing | JUnit has no `time` attribute (`write_junit`), test `test_no_timing_in_the_junit_or_output` |

Judging alignment: technological implementation is evidenced by a target agent and integrity checks built on QNX APIs; design by a complete tool (doctor, install, build, run, test, shell, lifecycle) with defined exit codes; potential impact by the missing inner loop and CI runner for QNX 8.0 on hosts the SDP does not support; innovation by a network-free, verified transport over the serial console only. These are the four equally weighted criteria in the public rules, not predicted scores.
