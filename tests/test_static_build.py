"""
The production static build.

Development and test settings both use plain ``StaticFilesStorage``, so nothing
in the suite used to exercise the manifest storage that production actually
uses. That gap let a broken ``collectstatic`` ship: the vendored Chart.js ended
with a ``sourceMappingURL`` comment pointing at a ``.map`` file that was never
committed, and ``CompressedManifestStaticFilesStorage`` refuses to resolve a
missing reference. The Docker build failed at the collectstatic layer, which
meant the production image could not be built at all.

These tests run the real thing, so the next dangling reference fails here rather
than in a deploy.
"""

import re
import tempfile
from pathlib import Path

from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

PRODUCTION_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

#: `//# sourceMappingURL=…` in JS, `/*# sourceMappingURL=… */` in CSS.
SOURCE_MAP_RE = re.compile(r"sourceMappingURL=(\S+?)(?:\s|\*/|$)")


class TestCollectstaticSucceeds(SimpleTestCase):
    """
    The command the Docker image runs at build time, run for real.

    This is the whole point of the file: it is slow-ish because it hashes and
    compresses every static file, and it is worth it because it is the only
    thing standing between a dangling URL reference and a failed deploy.
    """

    def test_the_production_static_build_completes(self):
        with tempfile.TemporaryDirectory() as static_root:
            with override_settings(STORAGES=PRODUCTION_STORAGE, STATIC_ROOT=static_root):
                # Raises MissingFileError if any CSS or JS file references
                # something that is not there.
                call_command("collectstatic", "--no-input", verbosity=0)

            manifest = Path(static_root) / "staticfiles.json"
            self.assertTrue(manifest.exists(), "no manifest was written")


class TestNoDanglingSourceMaps(SimpleTestCase):
    """
    A faster, more specific guard on the mistake that actually happened.

    Vendored libraries are shipped without their `.map` files — a megabyte of
    development aid nobody needs in a clinic. The comment naming them has to go
    with them, and it is easy to forget when upgrading a library.
    """

    def _static_sources(self):
        """Every CSS and JS file the finders can see, as absolute paths."""
        for finder in finders.get_finders():
            for relative, storage in finder.list([]):
                if relative.endswith((".js", ".css")):
                    yield relative, Path(storage.path(relative))

    def test_the_scan_actually_reaches_the_vendored_libraries(self):
        # Without this, the assertion below would pass just as happily against
        # an empty file list.
        names = {relative for relative, _ in self._static_sources()}
        self.assertIn("js/vendor/chart.umd.js", names)
        self.assertIn("js/growth_chart.js", names)

    def test_every_source_map_reference_resolves(self):
        for relative, path in self._static_sources():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for reference in SOURCE_MAP_RE.findall(text):
                if reference.startswith("data:"):
                    continue  # Inlined, so nothing to resolve.
                self.assertTrue(
                    (path.parent / reference).exists(),
                    f"{relative} names a source map that is not committed: "
                    f"{reference}. Strip the sourceMappingURL comment — see "
                    f"static/js/vendor/README.md.",
                )
