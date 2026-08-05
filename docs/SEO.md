# Being found on Google

Everything below is already in place and needs no work. This page exists for the
three things that **do** need a person, and for the one thing that must never be
added.

---

## Before the site goes live

### 1. Set `SITE_URL`

Without it, the site's own address is worked out from the incoming request. That
is right on a laptop and wrong behind a proxy, where the address a canonical link
and the sitemap advertise can end up being one the public cannot reach.

```
SITE_URL=https://cemhcare.com
```

No trailing slash. Set it in the same place as `ALLOWED_HOSTS`.

### 2. Put the clinic on the map

The map pin is **deliberately empty**. Nobody has surveyed the building, and a
guessed coordinate is a pin that patients then drive to.

Open Google Maps, right-click the clinic's actual doorway, and copy the two
numbers it shows:

```
CLINIC_LATITUDE=19.2307
CLINIC_LONGITUDE=72.8567
```

Until these are set, the listing works but has no map pin.

### 3. Claim the Google Business Profile

This is the single highest-value thing left, and it is not a code change.
Most people looking for an endocrinologist in Borivali will see the Google
Maps panel rather than a search result, and that panel comes from a Business
Profile, not from this website.

Create it at <https://business.google.com>, using **exactly** the name, address
and phone number this site publishes — Google matches the two, and a difference
in the wording of the address weakens both.

Once other listings exist (the Business Profile, Practo, Instagram), add them so
the site and the listings identify each other:

```
SOCIAL_PROFILES=https://g.page/...,https://www.practo.com/...
```

Only list pages the clinic actually controls.

---

## What is already done

| | |
|---|---|
| **Title, description, canonical** | One address for the page, so it does not compete with itself |
| **Structured data** | `MedicalClinic`, a `Physician` per doctor, `FAQPage`, `WebSite`, `WebPage` — one connected graph, server-rendered |
| **Opening hours, address, specialties** | Machine-readable, and taken from the same settings the page prints, so the two cannot drift |
| **Sharing preview** | `og:` and `twitter:` tags with a 1200×630 card, so a WhatsApp link looks like a clinic rather than spam |
| **Favicons** | SVG, `.ico` and an iOS home-screen icon |
| **robots.txt** | Staff areas excluded; stylesheet and images left crawlable so Google can render the page |
| **sitemap.xml** | At the root, pointed to from robots.txt |
| **Accessibility** | One `<h1>`, ordered headings, alt text on every image, keyboard-operable tabs and FAQs |
| **Speed** | No webfonts, no external requests, image dimensions declared so nothing jumps as it loads |

### Re-making the images

The sharing card and favicons are generated from the clinic's name, tagline and
colours. After changing any of those:

```
python manage.py make_brand_images
```

Then commit the four files in `static/` that it writes.

---

## What must never be added

**Ratings and reviews.**

There is nowhere in this system a real review can come from, so any
`aggregateRating` or `review` markup would be invented. It would make the
listing look better — five gold stars in the search result — and it is:

* against Google's structured data policy, and grounds for a manual action that
  removes the clinic from search entirely;
* a lie told to somebody choosing a doctor.

A test (`tests/test_seo.py::TestItInventsNothing`) fails if either appears. If
you are reading this because that test is failing, the answer is not to change
the test.

Real reviews on the Google Business Profile show up in search on their own, and
they are the honest route to the same thing.

The same rule covers awards, accreditations, insurance acceptance and prices:
if the clinic has not stated it, the site does not claim it.
