# qnxrun demo: shooting and render script

## Promise and shape

For QNX Global Hackathon judges evaluating developer tooling: show, in under three minutes, one command taking a C file to a QNX 8.0 target and the exit code back to the host shell, then the three things a test runner must get right: streaming, killing on timeout, refusing damaged bytes. End with the CI mode.

Shape: real terminal footage of the host, typed by `video/demo-session.sh` and recorded with asciinema, under English narration (`say`), no face, no music. Every command and every output line in the footage was executed during the recording. Idle gaps are capped by agg and each clip is paced to its narration, so no timing can be read from it and none is spoken.

## Starting state

- `qnxrun up` already done (the persistent target is running). `qnxrun test` in beat 5 makes its own clean boot.
- `tests/e2e/build/` holds the e2e binaries (`hello`, `stream`, `hang`, `victim`) built with `qnxrun build`.

## Beat sheet

The narration is verbatim in `video/demo.json`.

| Beat | On screen | Footage range (cast-marks) |
|---|---|---|
| 1. The problem; doctor | `qnxrun doctor`: seven `ok` lines | start to `cat hello.c` |
| 2. Compile, deliver, run, exit code | `cat hello.c`; `qnxrun run hello.c 7 "a b" '$(reboot)'`; `host rc=7` | to the `stream` command |
| 3. Streaming and timeout | `stream` line then rc 124; `hang` rc 124; `pidin` count 0 | to the `rm -f` command |
| 4. Damaged bytes never run | marker removed; proxy flips one byte: `REFUSED`, rc 123; the marker file is absent | to `qnxrun test` |
| 5. CI mode | clean boot, PASS/FAIL, `host rc=1`, JUnit failure element | to the end |
| 6. Proven, and the limits | closing card: repository, what was proven, limits | none |

## Record and render

```sh
asciinema rec --headless --overwrite -f asciicast-v2 --window-size 110x30 -c "bash video/demo-session.sh" demo.cast
agg --font-family Menlo --font-size 22 --theme asciinema --idle-time-limit 3 --fps-cap 15 --last-frame-duration 1 demo.cast demo.gif
ffmpeg -y -i demo.gif -vf "fps=15,scale=1700:-2:flags=lanczos,format=yuv420p" -c:v libx264 -crf 18 -tune stillimage demo-term.mp4
python3 <coldchain-guard>/video/cast-marks.py demo.cast 'cat hello.c' 'build/stream' 'rm -f /tmp' 'qnxrun test build' 'failure message'
python3 <video engine>/demo_slides.py video/demo.json --work <render dir>
```

The cast, GIF, MP4 and render files stay outside git.

## Review gates

- The footage shows `host rc=7`, two `rc 124`, `0` from `pidin`, `REFUSED` with `host rc=123`, `No such file` for the marker, and `host rc=1` from `qnxrun test`.
- No duration, timestamp or speed claim on screen or in the narration. The QNX boot banner is not shown.
- Contact sheet of the final video inspected; `ffprobe` duration strictly below 180 seconds, H.264 and AAC.
