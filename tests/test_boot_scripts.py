"""
The scripts that start the clinic system.

A checkout on Windows arrived with CRLF line endings, so the container read
``set -e\\r`` instead of ``set -e``. The shell reported "Illegal option -",
the entrypoint died on its first line, and the container restarted forever —
while ``docker compose`` reported "Started" and ``docker compose ps`` had to be
run to see it was lying.

Nothing in the suite could have caught that: every test ran on Linux, where the
files are correct, and the failure only existed in the working tree of whoever
had cloned the repository on Windows.

These run wherever the suite runs — including inside the container, against the
bind-mounted working tree, which is the copy that actually breaks.
"""

from pathlib import Path

from django.test import SimpleTestCase

REPO = Path(__file__).resolve().parent.parent

#: Read by a Linux shell, so a carriage return is a syntax error.
UNIX_SCRIPTS = sorted(
    [*REPO.glob("docker/*.sh"), *REPO.glob("*.command")]
)

FIX = (
    "\n\nThis file must use Unix (LF) line endings. To repair a checkout:\n"
    "    git add --renormalize .\n"
    "    git checkout -- .\n"
    "and confirm .gitattributes is present so it does not happen again."
)


class TestTheScriptsAShellHasToRead(SimpleTestCase):
    def test_there_are_some_to_check(self):
        # Guards the guard: a glob that matches nothing passes every test below
        # while proving nothing at all.
        self.assertTrue(UNIX_SCRIPTS, "no shell scripts found to check")

    def test_none_of_them_carry_carriage_returns(self):
        for script in UNIX_SCRIPTS:
            with self.subTest(script=script.relative_to(REPO)):
                body = script.read_bytes()
                self.assertNotIn(
                    b"\r", body,
                    f"{script.relative_to(REPO)} has Windows line endings, so the "
                    f"shell inside the container will refuse it.{FIX}",
                )

    def test_the_entrypoint_still_starts_with_set_e(self):
        # The line that failed. If it is ever moved or removed, the tests above
        # go on passing while guarding nothing that matters.
        entrypoint = REPO / "docker" / "entrypoint.sh"
        self.assertIn("set -e", entrypoint.read_text().splitlines())


class TestTheWindowsLaunchers(SimpleTestCase):
    """
    ``cmd.exe`` does not read UTF-8 under its default codepage, so a dash typed
    as an em dash reaches the receptionist as mojibake. One had already got into
    a line the launcher prints on screen while it waits.

    These files are the clinic's front door and are read by people who will not
    know that a stray character is cosmetic, so they stay plain ASCII.
    """

    def setUp(self):
        self.launchers = sorted(REPO.glob("*.bat"))
        self.assertTrue(self.launchers, "no .bat launchers found")

    def test_they_are_plain_ascii(self):
        for launcher in self.launchers:
            with self.subTest(launcher=launcher.name):
                try:
                    launcher.read_bytes().decode("ascii")
                except UnicodeDecodeError as exc:
                    self.fail(
                        f"{launcher.name} contains a character cmd.exe will "
                        f"mangle: {exc}. Use plain ASCII - a hyphen for a dash, "
                        f"straight quotes."
                    )

    def test_the_launcher_opens_the_browser_itself(self):
        body = (REPO / "START-CLINIC.bat").read_text()
        self.assertIn("start \"\" http://localhost:8000/", body)

    def test_the_launcher_waits_for_docker_rather_than_giving_up(self):
        # Straight after switch-on Docker Desktop is not ready for half a
        # minute, which is exactly when this runs if it is set to autostart.
        body = (REPO / "START-CLINIC.bat").read_text()
        self.assertIn(":dockerwait", body)

    def test_autostart_can_be_turned_on_and_off_again(self):
        # A one-way door into the Startup folder is not something to hand
        # somebody who does not use a terminal.
        self.assertTrue((REPO / "AUTOSTART-ON.bat").exists())
        self.assertTrue((REPO / "AUTOSTART-OFF.bat").exists())

    def test_autostart_points_at_the_launcher_beside_it(self):
        body = (REPO / "AUTOSTART-ON.bat").read_text()
        self.assertIn("'%~dp0START-CLINIC.bat'", body)
        self.assertIn("$s.Arguments = '/auto'", body)

    def test_an_autostarted_launcher_does_not_wait_for_a_keypress(self):
        # Nobody is sitting there at login to press one.
        body = (REPO / "START-CLINIC.bat").read_text()
        self.assertIn('if /i "%~1"=="/auto" exit /b 0', body)


class TestTheContainerOwnsItsBootScript(SimpleTestCase):
    """
    Correct line endings in the repository are not enough on their own.

    A repository can be unzipped, copied off a USB stick, or saved by an editor
    that knows better, and none of those consult .gitattributes. So the image
    carries its own normalised copy and boots from that, and the bind-mounted
    working tree cannot stop the clinic system from starting.
    """

    def setUp(self):
        self.dockerfile = (REPO / "Dockerfile").read_text()
        self.compose = (REPO / "docker-compose.yml").read_text()

    def test_the_image_takes_its_own_copy(self):
        self.assertIn(
            "COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh", self.dockerfile
        )

    def test_the_image_strips_carriage_returns_from_it(self):
        self.assertIn(r"sed -i 's/\r$//' /usr/local/bin/entrypoint.sh", self.dockerfile)

    def test_compose_boots_from_the_baked_copy_not_the_bind_mount(self):
        self.assertIn('entrypoint: ["sh", "/usr/local/bin/entrypoint.sh"]', self.compose)
        self.assertNotIn("/app/docker/entrypoint.sh", self.compose)


class TestGitKeepsThemThatWay(SimpleTestCase):
    """The root cause: without .gitattributes, Git on Windows rewrites them."""

    def setUp(self):
        path = REPO / ".gitattributes"
        self.assertTrue(path.exists(), ".gitattributes is missing")
        self.rules = path.read_text()

    def test_shell_scripts_are_pinned_to_lf(self):
        self.assertIn("*.sh        text eol=lf", self.rules)

    def test_the_mac_launchers_are_pinned_to_lf(self):
        self.assertIn("*.command   text eol=lf", self.rules)

    def test_windows_batch_files_are_left_as_crlf(self):
        # The one place CRLF is wanted: cmd.exe can mis-parse a LF-only .bat,
        # and these are what the clinic double-clicks every morning.
        self.assertIn("*.bat       text eol=crlf", self.rules)
