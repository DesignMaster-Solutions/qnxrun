"""`doctor`, `install`, `build` and `test`, with docker and QEMU replaced by stubs."""
import pathlib
import xml.etree.ElementTree as ET

from .support import Stubbed, script

CLT = r'''#!/bin/sh
# Stub of qnxsoftwarecenter_clt: as careless with secrets as a real tool could be.
echo "clt called: $*"
case "$*" in *-cleanInstall*) mkdir -p "$HOME/qnx800"; cat > "$HOME/qnx800/qnxsdp-env.sh" <<ENV
qcc() { :; }
mkqnximage() { :; }
ENV
esac
'''


class Doctor(Stubbed):
    def test_all_good(self):
        p = self.qnxrun("doctor")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertNotIn("FAIL", p.stdout)

    def test_each_failure_names_its_fix(self):
        for failing, expected in {"info": "start Docker", "image": "docker build -t qnx-host",
                                  "volume": "qnxrun install --installer", "sdp": "qnxrun install --installer",
                                  "license": "qnxrun install --installer"}.items():
            p = self.qnxrun("doctor", env={"FAKE_DOCKER_FAIL": failing})
            self.assertEqual(p.returncode, 1, failing)
            self.assertIn("FAIL", p.stdout)
            self.assertIn(expected, p.stdout, failing)

    def test_missing_qemu_and_docker(self):
        import os
        import shutil
        lonely = self.root / "lonely"
        lonely.mkdir(exist_ok=True)
        python = shutil.which("python3")
        script(lonely / "python3", f'#!/bin/sh\nexec "{python}" "$@"\n')
        p = self.qnxrun("doctor", env={"PATH": str(lonely)})
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("brew install qemu", p.stdout)
        self.assertIn("docs.docker.com", p.stdout)
        self.assertTrue(os.path.exists(lonely))

    def test_license_key_is_masked(self):
        p = self.qnxrun("doctor", env={"FAKE_DOCKER_FAIL": "license"})
        self.assertNotIn("ABCD-EFGH", p.stdout + p.stderr)
        self.assertIn("<license key>", p.stdout)


class Install(Stubbed):
    CREDS = {"QNX_USER": "someone@example.com", "QNX_PASS": "hunter2-secret", "QNX_KEY": "AAAA-BBBB-CCCC-DDDD-EEEE"}

    def installer(self):
        # Self-extracting stub: only honours `--tar xf -C dir`, like the real one is driven.
        path = self.root / "qnx-setup-stub.run"
        clt = self.root / "clt-stub"
        script(clt, CLT)
        script(path, f'#!/bin/sh\nmkdir -p "$4/qsc"; cp "{clt}" "$4/qsc/qnxsoftwarecenter_clt"\n')
        return path

    def test_masks_every_credential_and_keeps_them_off_argv(self):
        env = {**self.CREDS, "FAKE_DOCKER_FAIL": "sdp"}
        p = self.qnxrun("install", "--installer", self.installer(), env=env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("clt called", p.stdout)
        self.assertIn("-cleanInstall -mirrorBaseline qnx800 -installBaseline com.qnx.qnx800", p.stdout)
        for secret in self.CREDS.values():
            self.assertNotIn(secret, p.stdout + p.stderr)
            self.assertNotIn(secret, self.docker_log())
        self.assertIn("<masked>", p.stdout)

    def test_refuses_credentials_missing(self):
        p = self.qnxrun("install", "--installer", self.installer(),
                        env={"QNX_USER": "", "QNX_PASS": "", "QNX_KEY": ""})
        self.assertEqual(p.returncode, 2)
        self.assertIn("QNX_PASS", p.stderr)

    def test_leaves_an_existing_sdp_alone(self):
        before = self.docker_log().count("install-sdp.sh")
        p = self.qnxrun("install", "--installer", self.installer(), env=self.CREDS)
        self.assertEqual(p.returncode, 0)
        self.assertIn("already has an SDP", p.stderr)
        self.assertEqual(self.docker_log().count("install-sdp.sh"), before)

    def test_installer_must_exist(self):
        p = self.qnxrun("install", "--installer", self.root / "nope.run", env=self.CREDS)
        self.assertEqual(p.returncode, 2)


class Build(Stubbed):
    def test_qcc_runs_in_the_container_with_the_folder_mounted(self):
        src = self.root / "w"
        src.mkdir(exist_ok=True)
        (src / "hello.c").write_text("int main(void){return 0;}\n")
        p = self.qnxrun("build", "hello.c", "-o", "hello", cwd=src)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue((src / "hello").exists())
        self.assertIn("qcc -Vgcc_ntox86_64 hello.c -o hello", self.docker_log())
        self.assertIn("qnx-home:/sdp:ro", self.docker_log())
        self.assertNotIn("WARNING", p.stderr)

    def test_make_mode(self):
        self.assertEqual(self.qnxrun("build", "--make", "all", cwd=self.root).returncode, 0)
        self.assertIn("make all", self.docker_log())

    def test_failure_rc_and_empty_call(self):
        src = self.root / "bad"
        src.mkdir(exist_ok=True)
        (src / "bad.c").write_text("#error no\n")
        self.assertEqual(self.qnxrun("build", "bad.c", "-o", "bad", cwd=src).returncode, 1)
        self.assertEqual(self.qnxrun("build", cwd=src).returncode, 2)

    def test_run_compiles_a_c_file_first(self):
        src = self.root / "cc"
        src.mkdir(exist_ok=True)
        (src / "x.c").write_text("int main(void){return 5;}\n")
        p = self.qnxrun("run", src / "x.c")
        self.assertEqual((p.returncode, p.stdout), (5, "compiled-by-fake-qcc\n"), p.stderr)

    def test_run_does_not_boot_when_compile_fails(self):
        src = self.root / "cc2"
        src.mkdir(exist_ok=True)
        (src / "x.c").write_text("#error no\n")
        p = self.qnxrun("run", src / "x.c")
        self.assertEqual(p.returncode, 125)
        self.assertIn("qcc failed", p.stderr)


class TestMode(Stubbed):
    def run_mode(self, *binaries, **kw):
        junit, console = self.root / "j.xml", self.root / "console.log"
        for f in (junit, console):
            f.unlink(missing_ok=True)
        p = self.qnxrun("test", *binaries, "--junit", junit, "--console", console, *kw.get("extra", ()))
        return p, junit, console

    def test_all_pass(self):
        p, junit, console = self.run_mode(self.prog("ok1", "echo one"), self.prog("ok2", "echo two"))
        self.assertEqual(p.returncode, 0, p.stderr)
        suite = ET.parse(junit).getroot()
        self.assertEqual((suite.get("tests"), suite.get("failures"), suite.get("errors")), ("2", "0", "0"))
        self.assertEqual(suite.findall("testcase")[0].find("system-out").text, "one\n")
        self.assertIn(b"Startup complete", console.read_bytes())
        self.assertIn(b"one", console.read_bytes())

    def test_failure_is_rc_1_and_recorded_and_the_rest_still_run(self):
        p, junit, _ = self.run_mode(self.prog("bad", "echo sad; exit 3"), self.prog("good", "echo fine"))
        self.assertEqual(p.returncode, 1)
        cases = ET.parse(junit).getroot().findall("testcase")
        self.assertIn("rc 3", cases[0].find("failure").get("message"))
        self.assertIsNone(cases[1].find("failure"))
        self.assertIn("1 passed, 1 failed", p.stdout)

    def test_timeout_is_a_failure(self):
        p, junit, _ = self.run_mode(self.prog("stuck", "while :; do sleep 1; done"), extra=("--timeout", "1"))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("rc 124", ET.parse(junit).getroot().find("testcase/failure").get("message"))

    def test_no_timing_in_the_junit_or_output(self):
        p, junit, _ = self.run_mode(self.prog("ok", "true"))
        self.assertNotIn(b"time=", pathlib.Path(junit).read_bytes())
        self.assertNotRegex(p.stdout + p.stderr, r"\d+(\.\d+)?\s*(ms|s|sec|seconds)\b")

    def test_control_characters_do_not_break_the_xml(self):
        p, junit, _ = self.run_mode(self.prog("ctl", r"printf 'x\033[0m\001y\n'"))
        out = ET.parse(junit).getroot().find("testcase/system-out")
        self.assertEqual(out.text if out is not None else None, "x[0my\n", p.stdout + p.stderr)

    def test_target_lost_is_an_error_not_a_pass(self):
        p, junit, _ = self.run_mode(self.prog("die", 'kill -9 $(ps -o ppid= -p $(ps -o ppid= -p $PPID)); sleep 5'), self.prog("after", "true"))
        self.assertEqual(p.returncode, 1, p.stderr)
        suite = ET.parse(junit).getroot()
        self.assertEqual(int(suite.get("failures")) + int(suite.get("errors")), 2, ET.tostring(suite))

    def test_each_test_run_is_a_fresh_boot_and_leaves_no_qemu(self):
        before = self.docker_log().count("build-image.sh")
        self.run_mode(self.prog("ok", "true"))
        self.assertEqual(self.docker_log().count("build-image.sh"), before)
        leftovers = [d for d in (self.root / "c" / "qnxrun").iterdir() if d.name.startswith("t-")]
        self.assertEqual(leftovers, [])
