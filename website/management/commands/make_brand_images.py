"""
Generate the sharing card and the favicons from the clinic's own branding.

These are committed files, not something rendered per request — a link preview
is fetched by WhatsApp or Google, not by a browser with a session, and it has to
be a plain image at a stable URL.

Run it after changing the clinic's name, tagline or colours:

    python manage.py make_brand_images

The sharing card matters more here than it usually would. This clinic tells
patients to book over WhatsApp, and a WhatsApp message carrying a bare link with
no preview looks like spam — which is the opposite of what a clinic sending its
own address wants.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from PIL import Image, ImageDraw, ImageFont

#: Matches static/css/website.css. Stated again rather than parsed out of the
#: stylesheet: a regular expression over CSS is a thing that breaks quietly.
TEAL = (47, 125, 114)
TEAL_DARK = (31, 90, 82)
TEAL_DEEP = (20, 62, 56)
CREAM = (251, 250, 247)
ACCENT = (212, 103, 79)
SUN = (217, 164, 65)

#: Facebook, WhatsApp, LinkedIn and X all crop to about 1.91:1.
CARD = (1200, 630)

#: Tried in order. The clinic's machine is not this one, so nothing may assume
#: a particular font is installed — the last resort is Pillow's own bitmap font,
#: which is ugly but never absent.
SERIF_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "C:/Windows/Fonts/georgiab.ttf",
]
SANS_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _font(candidates, size):
    for path in candidates:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap(draw, text, font, max_width):
    """Greedy wrap, measured against the font actually chosen."""
    words = text.split()
    lines, line = [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _vertical_gradient(size, top, bottom):
    width, height = size
    base = Image.new("RGB", (1, height))
    pixels = base.load()
    for y in range(height):
        ratio = y / max(height - 1, 1)
        pixels[0, y] = tuple(
            round(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3)
        )
    return base.resize(size, Image.Resampling.BILINEAR)


def _mark(draw, cx, cy, scale, colour):
    """
    The clinic mark, simplified.

    The real logo is a set of bezier paths in templates/branding/logo.html.
    Reproducing those in Pillow would be a second copy to keep in step, so this
    draws the same structure — a stem, a head and two lobes — at a size where
    that is all anybody can see anyway.
    """
    r = scale
    draw.ellipse([cx - r * 0.16, cy - r * 1.05, cx + r * 0.16, cy - r * 0.62],
                 fill=colour)
    draw.line([(cx, cy - r * 0.62), (cx, cy - r * 0.12)],
              fill=colour, width=max(2, round(r * 0.14)))
    draw.ellipse([cx - r * 0.92, cy - r * 0.20, cx - r * 0.04, cy + r * 0.86],
                 fill=colour)
    draw.ellipse([cx + r * 0.04, cy - r * 0.20, cx + r * 0.92, cy + r * 0.86],
                 fill=colour)


class Command(BaseCommand):
    help = "Generate the link-preview card and favicons from the clinic branding."

    def handle(self, *args, **options):
        static_dir = Path(settings.BASE_DIR) / "static"
        static_dir.mkdir(exist_ok=True)

        self._sharing_card(static_dir / "og-image.png")
        self._favicon_svg(static_dir / "favicon.svg")
        self._raster_icons(static_dir)

        self.stdout.write(self.style.SUCCESS(
            "Wrote og-image.png, favicon.svg, favicon.ico and apple-touch-icon.png "
            "into static/. Run collectstatic to publish them."
        ))

    # ── The sharing card ─────────────────────────────────────────────────────

    def _sharing_card(self, path):
        clinic = settings.CLINIC
        image = _vertical_gradient(CARD, TEAL_DARK, TEAL_DEEP)
        draw = ImageDraw.Draw(image)
        width, height = CARD

        # A band of the brand gradient across the top, so a preview thumbnail
        # still reads as this clinic rather than as a dark rectangle.
        for x in range(width):
            ratio = x / width
            if ratio < 0.55:
                mix = ratio / 0.55
                colour = tuple(round(TEAL[i] + (SUN[i] - TEAL[i]) * mix) for i in range(3))
            else:
                mix = (ratio - 0.55) / 0.45
                colour = tuple(round(SUN[i] + (ACCENT[i] - SUN[i]) * mix) for i in range(3))
            draw.line([(x, 0), (x, 9)], fill=colour)

        margin = 84
        _mark(draw, margin + 44, 148, 46, CREAM)

        name_font = _font(SERIF_FONTS, 76)
        tagline_font = _font(SANS_FONTS, 34)
        detail_font = _font(SANS_FONTS, 28)

        y = 232
        for line in _wrap(draw, clinic.CLINIC_NAME, name_font, width - margin * 2):
            draw.text((margin, y), line, font=name_font, fill=CREAM)
            y += 88

        y += 14
        draw.text((margin, y), clinic.CLINIC_TAGLINE, font=tagline_font,
                  fill=(203, 228, 222))

        # The two facts somebody scanning a shared link actually wants.
        where = ", ".join(part for part in (clinic.CLINIC_CITY, clinic.CLINIC_REGION) if part)
        footer = " · ".join(part for part in (where, clinic.CLINIC_PHONE) if part)
        draw.line([(margin, height - 108), (width - margin, height - 108)],
                  fill=(58, 110, 102), width=2)
        draw.text((margin, height - 82), footer, font=detail_font, fill=(178, 208, 201))

        image.save(path, "PNG", optimize=True)

    # ── Favicons ─────────────────────────────────────────────────────────────

    def _favicon_svg(self, path):
        """
        The crisp one. Every current browser prefers an SVG icon, and it stays
        sharp on a high-resolution screen at any size.

        `prefers-color-scheme` is honoured, because a dark-teal mark on the dark
        strip of a browser in dark mode is a mark nobody can see.
        """
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n'
            "  <style>\n"
            "    .mark { fill: #2f7d72; }\n"
            "    @media (prefers-color-scheme: dark) { .mark { fill: #7fd8c8; } }\n"
            "  </style>\n"
            '  <g class="mark">\n'
            '    <circle cx="32" cy="15" r="5"/>\n'
            '    <rect x="29.5" y="17" width="5" height="14" rx="2.5"/>\n'
            '    <path d="M32 30c-4-6-11-8-15-4-4.5 4.5-3 14 2 18 5 4 12 2 13-5 '
            '.5-3.5 0-6 0-9z"/>\n'
            '    <path d="M32 30c4-6 11-8 15-4 4.5 4.5 3 14-2 18-5 4-12 2-13-5'
            '-.5-3.5 0-6 0-9z"/>\n'
            "  </g>\n"
            "</svg>\n",
            encoding="utf-8",
        )

    def _raster_icons(self, static_dir):
        """
        The fallbacks: a .ico for browsers that ignore SVG icons, and a
        180px PNG for an iPhone home screen, where the icon sits on the
        user's wallpaper and needs its own background.
        """
        def tile(size, background, foreground):
            image = Image.new("RGBA", (size, size), background)
            draw = ImageDraw.Draw(image)
            _mark(draw, size / 2, size / 2 + size * 0.04, size * 0.34, foreground)
            return image

        # Transparent, so it sits on a browser tab of any colour.
        icon = tile(256, (0, 0, 0, 0), (*TEAL, 255))
        icon.save(static_dir / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])

        # Opaque: iOS puts no background behind a home-screen icon, so a
        # transparent one comes out as a black square.
        apple = tile(180, (*CREAM, 255), (*TEAL, 255))
        apple.save(static_dir / "apple-touch-icon.png", "PNG", optimize=True)
