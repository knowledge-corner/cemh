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


class TestTheLauncherKeepsTheDatabaseInStep(SimpleTestCase):
    """
    A clinic machine crashed with "column accounts_doctorprofile.category does
    not exist" after pulling this batch.

    The container applies migrations when it starts, but the development server
    reloads changed Python without restarting the container. So a git pull put
    new code live against an old database, and every page that touched the new
    column died. The launcher applies migrations itself now, which makes
    pulling and double-clicking sufficient on its own.
    """

    def setUp(self):
        self.windows = (REPO / "START-CLINIC.bat").read_text()
        self.mac = (REPO / "START-CLINIC.command").read_text()

    def test_the_windows_launcher_applies_migrations(self):
        self.assertIn("manage.py migrate --no-input", self.windows)

    def test_the_mac_launcher_applies_migrations(self):
        self.assertIn("manage.py migrate --no-input", self.mac)

    def test_it_happens_before_the_browser_is_opened(self):
        # Otherwise the receptionist is looking at the error page while the
        # migration she needed runs behind it.
        migrate = self.windows.index("manage.py migrate --no-input")
        browser = self.windows.index('start "" http://localhost:8000/')
        self.assertLess(migrate, browser)

    def test_a_failure_to_migrate_does_not_stop_the_system_starting(self):
        # A clinic that cannot open the system at all is worse off than one
        # running with a warning it can act on.
        self.assertIn("The system may still work", self.windows)


class TestTheLauncherFetchesTheLatestVersion(SimpleTestCase):
    """
    The clinic's whole job is now: start Docker, double-click the launcher.

    That only holds if the launcher fetches the code itself. A separate "git
    pull" step to remember is a step that gets skipped, and the day it is
    skipped is the day new code meets an old database.
    """

    def setUp(self):
        self.windows = (REPO / "START-CLINIC.bat").read_text()
        self.mac = (REPO / "START-CLINIC.command").read_text()

    def test_both_launchers_pull(self):
        for name, body in (("Windows", self.windows), ("Mac", self.mac)):
            with self.subTest(launcher=name):
                self.assertIn("git pull --ff-only", body)

    def test_the_pull_never_merges(self):
        # This computer only ever receives changes. A pull that cannot simply
        # move forward is a situation for a human, not something to resolve
        # automatically at half past eight in the morning.
        for name, body in (("Windows", self.windows), ("Mac", self.mac)):
            with self.subTest(launcher=name):
                self.assertNotIn("git pull\n", body)
                self.assertNotIn("git merge", body)
                self.assertNotIn("git reset --hard", body)

    def test_git_cannot_sit_waiting_for_a_password(self):
        # Nobody is watching this window at 8am. A git that blocks on a
        # credential prompt never opens the clinic at all.
        for name, body in (("Windows", self.windows), ("Mac", self.mac)):
            with self.subTest(launcher=name):
                self.assertIn("GIT_TERMINAL_PROMPT=0", body)

    def test_a_stalled_network_gives_up_rather_than_hanging(self):
        for name, body in (("Windows", self.windows), ("Mac", self.mac)):
            with self.subTest(launcher=name):
                self.assertIn("GIT_HTTP_LOW_SPEED_TIME", body)

    def test_a_failed_pull_does_not_stop_the_clinic_opening(self):
        # Yesterday's code running is far better than no clinic system at all.
        for name, body in (("Windows", self.windows), ("Mac", self.mac)):
            with self.subTest(launcher=name):
                self.assertIn("carrying on with the version already", body)

    def _pull_runs_at(self, body, mac):
        """
        Where the pull actually *happens*.

        Not the position of the words "git pull" in both files. On the Mac the
        pull lives inside update_to_latest(), which is defined near the top
        whatever order things run in — so its text position proves nothing. It
        has to be the call site. An earlier version of these tests got this
        wrong and passed happily with the pull moved to after the build.
        """
        return body.index("  update_to_latest\n") if mac else body.index("git pull")

    def test_the_mac_launcher_actually_calls_the_update(self):
        # A function nobody calls does nothing, and every assertion about its
        # contents would still pass.
        self.assertIn("  update_to_latest\n", self.mac)

    def test_it_pulls_before_it_builds(self):
        # A pull that changes the Dockerfile or the requirements needs the
        # image rebuilt. Pulling afterwards would run new code against the old
        # dependencies.
        for name, body, mac in (("Windows", self.windows, False),
                                ("Mac", self.mac, True)):
            with self.subTest(launcher=name):
                self.assertLess(
                    self._pull_runs_at(body, mac), body.index("compose up")
                )

    def test_it_pulls_before_it_migrates(self):
        # The migration has to be the one the new code brought with it.
        for name, body, mac in (("Windows", self.windows, False),
                                ("Mac", self.mac, True)):
            with self.subTest(launcher=name):
                self.assertLess(
                    self._pull_runs_at(body, mac), body.index("manage.py migrate")
                )

    def test_the_build_is_not_skipped(self):
        for name, body in (("Windows", self.windows), ("Mac", self.mac)):
            with self.subTest(launcher=name):
                self.assertIn("compose up -d --build", body)

    def test_a_copy_that_is_not_a_checkout_still_starts(self):
        # The repository can arrive as a zip or off a USB stick, with no .git
        # and possibly no git installed. Neither is a reason not to open.
        self.assertIn('if not exist ".git"', self.windows)
        self.assertIn("[ -e .git ]", self.mac)
        self.assertIn("where git", self.windows)
        self.assertIn("command -v git", self.mac)


class TestTheLaunchersSurviveUpdatingThemselves(SimpleTestCase):
    """
    A launcher that pulls can pull a change *to itself*, mid-run.

    Both cmd.exe and sh read a script incrementally, keeping their place by
    byte offset. Rewrite the file underneath a running one and it resumes at
    the wrong spot and executes nonsense — on the clinic's machine, at opening
    time, with no developer in the room. Each shell needs its own answer.
    """

    def setUp(self):
        self.windows = (REPO / "START-CLINIC.bat").read_text()
        self.mac = (REPO / "START-CLINIC.command").read_text()

    def test_the_mac_launcher_reads_itself_fully_before_running(self):
        # sh parses a function definition to its closing brace before executing
        # anything, so wrapping the body means the whole file is in memory
        # before the pull can touch it.
        self.assertIn("main() {", self.mac)
        self.assertIn('main "$@"', self.mac)

    def test_nothing_of_substance_follows_the_call_on_the_mac(self):
        # The guarantee only holds while `main "$@"` is last. A line added
        # after it is a line read from disk *after* the pull has rewritten it.
        after = self.mac.split('main "$@"')[-1]
        remaining = [
            line for line in after.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertEqual(remaining, [], f"code after main: {remaining}")

    def test_the_windows_launcher_restarts_itself_when_it_changed(self):
        # cmd.exe has no equivalent of the function trick, so it compares the
        # commit before and after and starts again in a fresh window.
        self.assertIn("git rev-parse HEAD", self.windows)
        self.assertIn('start "" "%~f0"', self.windows)

    def test_the_restart_cannot_loop_forever(self):
        # The flag is an environment variable precisely so the new process
        # inherits it; an argument would collide with /auto.
        self.assertIn("if defined CLINIC_UPDATED", self.windows)
        self.assertIn("set CLINIC_UPDATED=1", self.windows)

    def test_the_restart_keeps_the_autostart_flag(self):
        # Started from the Startup folder it is passed /auto, and a restart
        # that dropped it would leave a window waiting for a keypress at login.
        restart = self.windows.index('start "" "%~f0"')
        self.assertIn("%*", self.windows[restart:restart + 40])
