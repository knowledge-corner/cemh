# CEMH — public website

The Centre for Endocrine & Metabolic Health's public site. Plain HTML, CSS and
JavaScript. **No build step, no framework, no server, no database** — every file
here is served exactly as it sits, which is what GitHub Pages needs.

It is completely independent of the clinic management system on the `main`
branch. Neither one calls the other.

```
index.html            the whole page
CNAME                 the custom domain GitHub Pages serves this at
robots.txt
sitemap.xml
.nojekyll             tells GitHub Pages to publish the files as they are
assets/
  css/website.css
  js/website.js       mobile menu and the services tabs; the page works without it
  photos/             the doctors' photographs
  og-image.png        the preview shown when the link is shared
  favicon.svg  favicon.ico  apple-touch-icon.png
```

**To look at it:** double-click `index.html`. That is all.

---

## Publishing on GitHub Pages

1. **Settings → Pages**
2. **Source:** Deploy from a branch
3. **Branch:** `website`, folder `/ (root)`
4. Save. The first build takes a minute or two.

With the `CNAME` file present it is served at `https://www.cemhcare.com/` once
DNS is pointed at GitHub. Until then, `https://knowledge-corner.github.io/cmeh/`
also works.

### The domain

`CNAME` in this folder says **`www.cemhcare.com`**, so that is the address the
site will be served at, and every absolute address in the page — the canonical
link, the sharing preview, the sitemap — has been set to match it exactly.

**They have to keep matching.** `cemhcare.com` and `www.cemhcare.com` are two
different addresses to a search engine, and a canonical link pointing at one
while the site is served from the other splits the page against itself and
breaks the WhatsApp preview image. If the CNAME ever changes, change these too:

```
sed -i 's#https://www.cemhcare.com#https://THE-NEW-ADDRESS#g' \
  index.html robots.txt sitemap.xml
```

Still to do at the registrar: add the DNS records GitHub shows under
Settings → Pages, then tick **Enforce HTTPS** once the certificate is issued.
Point the bare `cemhcare.com` at `www` as well, so somebody typing it without
the prefix still arrives.

After publishing, submit the sitemap in
[Google Search Console](https://search.google.com/search-console).

---

## ⚠️ The doctors' photographs are placeholders

`assets/photos/adway-kulkarni.jpg` and `vrushali-kulkarni.jpg` came from the
design mock-up. **They are AI-generated images of people who do not exist** —
one still carries a watermark in the corner.

Publishing an invented person under a named doctor's credentials is a
misrepresentation whatever the intent, and on a medical site it is worse than
most. Replace both before this goes live.

### Changing a photograph

Drop the new file into `assets/photos/` **keeping the same filename**. Nothing
else needs editing:

```
assets/photos/adway-kulkarni.jpg
assets/photos/vrushali-kulkarni.jpg
```

What works best: portrait shape, face in the upper third, about 900px wide or
more, saved under ~300KB. The page crops it to a tall portrait on a computer and
to a wide band on a phone.

If you use a different filename, edit the matching `src="..."` in `index.html` —
there is a comment directly above the doctors' list saying exactly where.

---

## Changing anything else

Everything is in `index.html`, in the order it appears on the page: header, hero,
about, doctors, services, why-us, FAQs, contact, footer.

**A doctor's details.** Each doctor is one `<article class="wf-doctor">` block:
name, role, qualifications, phone, email, two paragraphs, then the columns —
*Areas of focus*, *Qualifications*, and *Further training* where there is any.
Edit the list items directly.

Then update the matching `"employee"` entry in the JSON-LD block near the top of
the file. That is what Google reads, and a detail in one place but not the other
is worse than the detail in neither.

**A doctor joins or leaves.** Copy an existing `<article>` block, change it, give
it a new `id="doctor-N"`, and add or remove the matching JSON-LD entry.

**Phone number or address.** These appear in the header, the contact card, the
footer and the JSON-LD. Search the file for the old value and change every one.

**The map.** The `<iframe>` in the contact section uses Google's key-free embed —
the address goes in the `q=` parameter and Google finds it. To move the pin, edit
the address in both the `src` *and* the "Open in Google Maps" link below it, or
they will point at different places. For a pin on the exact doorway rather than
the building, open Google Maps, find the clinic, choose **Share → Embed a map**,
and paste that iframe over this one.

**Colours.** All named at the top of `assets/css/website.css`.

---

## Booking

There is no form. The page offers **WhatsApp** and the clinic's telephone
numbers, both of which reach a person immediately.

A GitHub Pages site is files only — it cannot receive a form submission or send
an email, because there is nothing running to do it. And a form that silently
goes nowhere is worse than no form: the visitor is told the clinic will ring, and
nothing rings.

If a written enquiry is wanted later, the two routes that work on a static site
are a Google Form embed or a form service such as Formspree. Both need somebody
watching where the answers land.

---

## What is already handled

Title, description and canonical link; Open Graph and Twitter cards with a
sharing image; favicons; `robots.txt` and `sitemap.xml`; and schema.org
structured data describing the clinic, both doctors and the FAQs — which is what
turns a search result into a listing with named consultants.

One `<h1>`, ordered headings, alt text on every image, keyboard-operable tabs and
FAQs, no webfonts. The only external request on the page is the Google map in the
contact section, and it is lazy-loaded so it cannot hold up the rest.

**There are deliberately no ratings or reviews in the structured data.** There is
no real source for them, so any number would be invented — which is against
Google's policy, grounds for removal from search, and a lie told to somebody
choosing a doctor. Real reviews on the clinic's Google Business Profile appear in
search on their own, and are the honest route to the same thing.

**Consulting hours appear nowhere**, at the clinic's request — including in the
structured data, so Google cannot publish hours the page itself does not state.


---

## Two files here that this site does not use

`requirements.txt` and `prod.txt` list Python packages — Django, gunicorn,
psycopg2 and so on. **GitHub Pages does not run Python**, so nothing reads them
and nothing installs them; they are left over from when this was going to be
hosted somewhere that runs code.

They are harmless, and they have been left alone rather than deleted because
they were not mine to remove. But they are misleading to the next person who
opens this folder, and their pinned versions no longer match the clinic
management system on `main`. Worth deleting once you are sure nothing else
wants them.
