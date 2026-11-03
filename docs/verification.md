# Verification record

What was run, where, and what came back. Every check below was seen failing before it was accepted as passing. Dates are November 2–3, 2026. No timing is recorded here on purpose: qnxrun publishes no measurement of QNX.

## Environment

- Target: QNX OS 8.0.0 x86_64 (`uname -a`: `QNX qnxrun 8.0.0 ... x86pc x86_64`), image built by `mkqnximage --type=qemu --arch=x86_64 --ssh-ident=none` with `qnxrun-agent` compiled by `qcc -Vgcc_ntox86_64`, booted by QEMU with `-snapshot`, no network, serial console on a Unix socket.
- SDP: QNX SDP 8.0 in a Docker volume, mounted read-only, inside an Ubuntu 24.04 linux/amd64 container, on a macOS arm64 host.
- Stub suite: macOS (local, Python 3.15), Linux (`python:3.12` container, local) and GitHub Actions (`ubuntu-latest`, `macos-latest`, Python 3.12).

## End to end on QNX (`QNXRUN_E2E=1 python3 -m unittest tests.e2e.test_qnx -v`)

Final run on November 3, 2026, after the last change to the agent: 9 tests, `OK`, rc 0. The suite was red before (the host-binary case, see below) and each case was turned red by a mutant.

| Check | Host receives |
|---|---|
| `hello 7` built by `qcc` | rc 7, `hello from QNX 8.0.0` on stdout |
| arguments `0`, `a b`, `$(reboot)`, `'q'`, `d"q`, `*`, empty | each printed literally, rc 0 |
| `stream`: one line, then `pause()` forever, `--timeout 120` | the line arrives while the host process is still running, then rc 124 |
| `hang`: ignores `SIGTERM`, forks a spinning child, `--timeout 5` | rc 124; `pidin -f a \| grep -c hang` on the target prints 0 |
| `segv` | rc 139 |
| `/bin/ls` from the Mac | rc 126 |
| `victim.c` (compiled by `run`) | rc 3 |
| `victim` through a proxy that flips one base64 byte of the transfer | rc 123, `REFUSED`; `/tmp/qnxrun-victim-ran` absent on the target; nothing left in `/tmp/qnxrun`; the same file over the intact link: rc 3 |
| `qnxrun test hello victim --junit` | rc 1, 2 tests, 1 failure `exit 3 (rc 3)`, console artifact contains the boot, no `time=` in the XML |

## Mutants

Copies of the repository outside it, one change each, nothing committed (the script is not part of the repository). Each was run against the stub suite and, where the change is on the target or the exit path, the relevant e2e test on QNX.

| Mutant | Change | Stub suite | QNX e2e |
|---|---|---|---|
| exit code always 0 | `Result.rc` returns 0 for `exit` (`qnxrun/console.py`) | rc 1: 7 failures, including `test_exit_code_reaches_the_host` (`0 != 7`) | rc 1: `test_exit_code_7_reaches_the_host`, `0 != 7` |
| hash not compared | `if (strcmp(got, want) != 0)` → `if (0)` (`qnxrun-agent.c`) | rc 1: `test_adulterated_binary_is_refused_and_never_runs` | rc 1: `test_adulterated_bytes_are_refused`, `126 != 123`: the damaged file was installed and an exec was attempted |
| timeout ignored | `deadline = 0` (`qnxrun-agent.c`) | rc 1: `test_timeout_kills_on_target_with_own_rc` (`125 != 124`), `test_timeout_is_a_failure` | rc 1: `test_timeout_kills_the_hung_program_on_the_target`, `125 != 124`: only the host watchdog ended it |
| host watchdog removed (found by our R310 review) | `deadline = None` in `Serial.run` (`qnxrun/console.py`) | survived all 57 tests at first; with the new `test_frozen_agent_is_an_error_after_timeout_plus_grace` (the program sends `SIGSTOP` to its own agent): rc 1, the host hangs until the test's subprocess limit (`TimeoutExpired`); real code: OK, rc 125 with `no answer from the target` | not run: stub-only, the watchdog is host code |

## Failures found on the way (red before green)

- `qnxrun doctor` against the real SDP first reported the license as invalid: `qcc` takes a lock in `~/.qnx/license`, and the volume was read-only. Fixed by copying the license files into the throwaway container home (`PREP` in `qnxrun/sdp.py`); the volume stays read-only and the check passes.
- On QNX, a file without an ELF or `#!` header is handed to `sh` by exec: `/bin/ls` from the Mac ran as a script and returned rc 1 (`syntax error`). The agent now refuses such files with `ENOEXEC` (rc 126). Stub test `test_text_that_sh_would_run_is_still_126` and e2e `test_host_binary_is_not_executed`.
- `qnxrun build -Wall ...` was rejected by argparse. Everything after `build` is now passed through literally.
- CI on Ubuntu failed: glibc declares `posix_openpt` only with `_GNU_SOURCE` (added to the native test build; QNX uses `_QNX_SOURCE`). The stand-in for QEMU also crashed when a client closed with unread data and announced the boot before its shell was ready; both were fixed in the stub, and the Linux suite was run three times in a row.

## Install

`qnxrun install` is tested only against a stub `qnxsoftwarecenter_clt` (`tests/test_host.py`, `Install`). The stub prints the user, password and key in clear: none of them appear in qnxrun's output or in the Docker command line. A real installation through qnxrun has not been run; the same `qnxsoftwarecenter_clt` sequence (`-addLicenseKey`, then `-cleanInstall -mirrorBaseline qnx800 -installBaseline com.qnx.qnx800`) was proven by our Cold-Chain Guard project's installer script.

## Not proven

- `examples/github-actions-self-hosted.yml` on any runner.
- A Linux x86_64 host with the real SDP (only the stub suite ran on Linux).
- File ownership hand-back on Linux bind mounts (`QNXRUN_OWNER`), stubs only.
