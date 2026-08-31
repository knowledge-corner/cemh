"""
Matching a doctor to their photograph.

The clinic keeps photographs in a ``photos/`` folder at the top of the
repository, and a picture is claimed by naming the file after the doctor —
``vrushali-kulkarni.jpg``. Nothing needs editing to add one; the folder is the
interface.

Resolved here rather than guessed at in the template, because a template that
writes ``photos/{{ slug }}.jpg`` produces a broken image for every doctor who
has not had a photograph taken yet, and a broken image on a clinic's front page
reads as a broken clinic.
"""

from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.utils.text import slugify

#: Tried in order, so a JPEG wins over a PNG of the same name.
EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")

PHOTO_DIR = "photos"


def candidate_names(user):
    """
    The filenames that would be understood as this doctor's photograph.

    "Dr. Vrushali Kulkarni" is looked up as ``vrushali-kulkarni``, and also as
    her username, because whoever drops the file in will use whichever of those
    they happen to have.
    """
    names = []
    full = slugify(f"{user.first_name} {user.last_name}".strip())
    if full:
        names.append(full)
    username = slugify(user.get_username())
    if username and username not in names:
        names.append(username)
    return names


def photo_url(user):
    """
    The URL of this doctor's photograph, or ``None`` if there is not one.

    An uploaded ``DoctorProfile.photo`` wins when there is one — uploading
    through the doctor's own admin page needs no server access and no
    rebuild, unlike the folder convention below. That convention still
    resolves photos placed directly in the photos/ folder (how the two
    placeholder images that ship with the app are found), so it stays as the
    fallback rather than being replaced.
    """
    profile = getattr(user, "doctor_profile", None)
    photo = getattr(profile, "photo", None)
    if photo:
        return photo.url

    for name in candidate_names(user):
        for extension in EXTENSIONS:
            path = f"{PHOTO_DIR}/{name}{extension}"
            if finders.find(path):
                return static(path)
    return None


def initials(user):
    """The fallback when no photograph exists — better than an empty grey box."""
    parts = [p for p in (user.first_name, user.last_name) if p]
    if not parts:
        return (user.get_username()[:2] or "?").upper()
    return "".join(p[0] for p in parts[:2]).upper()


def available_photos():
    """
    Every file currently in the photos folder.

    Used by the check that warns when a photograph has been added under a name
    no doctor answers to — the failure mode of a convention-based folder is a
    file that silently does nothing.
    """
    folder = Path(settings.BASE_DIR) / PHOTO_DIR
    if not folder.is_dir():
        return []
    return sorted(
        p.name for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in EXTENSIONS
    )
