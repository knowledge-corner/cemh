"""
robots.txt and sitemap.xml.

Both are plain text served from a view rather than files on disk, because both
have to carry the site's own absolute address and that is configuration, not
something to hard-code into a file somebody will forget exists.

A note on what robots.txt is and is not. It tells well-behaved crawlers where
not to go; it is **not** a security boundary, and nothing here is relied on to
keep patient data private. That job is done by the login — every private URL
redirects an anonymous request — and by the `noindex, nofollow, noarchive` meta
tag on every page behind it. The Disallow list below is a second, weaker layer
that keeps the clinic's own screens out of search results, and is written so
that getting it wrong cannot accidentally hide the public page.
"""

from django.conf import settings
from django.http import HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_GET

from . import seo

#: Everything a signed-in member of staff sees. Prefixes, so a URL added under
#: one of these later is covered without anybody remembering to come back here.
#:
#: Written as an explicit list rather than "Disallow: / then allow the home
#: page". That formulation depends on the crawler supporting Allow and the `$`
#: anchor, and a crawler that does not support them reads it as "index nothing"
#: — which silently removes the clinic from search altogether. The failure mode
#: of this version is a staff URL appearing in a search result, which is
#: visible, harmless and fixable.
PRIVATE_PREFIXES = [
    "/reception/",
    "/doctor/",
    "/calendar/",
    "/print/",
    "/activate/",
    "/login",
    "/logout",
    "/dashboard",
    "/media/",
    "/admin/",
    settings.ADMIN_URL if settings.ADMIN_URL.startswith("/") else f"/{settings.ADMIN_URL}",
]


@require_GET
def robots_txt(request):
    base = seo.site_url(request)
    lines = ["User-agent: *"]
    lines += [f"Disallow: {prefix}" for prefix in PRIVATE_PREFIXES]
    lines += [
        "",
        # The images and stylesheet must stay crawlable. Google renders a page
        # before judging it, and a page whose CSS it was refused looks broken
        # and unresponsive to the thing deciding how it ranks.
        "Allow: /static/",
        "",
        f"Sitemap: {base}/sitemap.xml",
        "",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


@require_GET
def sitemap_xml(request):
    """
    The sitemap.

    Hand-written rather than django.contrib.sitemaps, which would drag in the
    Sites framework and a SITE_ID for a single URL — and then take the domain
    from a database row that says "example.com" until somebody notices.

    No `lastmod`. The honest value would be the date the clinic last changed a
    doctor or a bio, and nothing records that. An invented one is a claim to a
    crawler that this page is fresh, which is the sort of small lie that gets
    the whole file discounted.
    """
    home = seo.absolute(request, reverse("website_home"))
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url>\n"
        f"    <loc>{home}</loc>\n"
        "    <changefreq>monthly</changefreq>\n"
        "    <priority>1.0</priority>\n"
        "  </url>\n"
        "</urlset>\n"
    )
    return HttpResponse(xml, content_type="application/xml; charset=utf-8")
