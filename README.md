# CEMH — public website

The Centre for Endocrine & Metabolic Health's public site. Plain HTML, CSS and
JavaScript. No build step, no framework, no server, no database.

It is **completely independent of the clinic management system**, which lives on
the `main` branch of this repository and is deployed somewhere else entirely.
Neither one calls the other. Changing this site cannot affect patient records,
and changing the management system cannot affect this site.

```
index.html            the whole page
robots.txt
sitemap.xml
assets/
  css/website.css
  js/website.js       mobile menu and the services tabs; the page works without it
  photos/             the doctors' photographs
  og-image.png        the preview shown when the link is shared
  favicon.svg  favicon.ico  apple-touch-icon.png
```

---

## Looking at it

Double-click `index.html`. That is genuinely all — it runs from a folder, a USB
stick or a web host without changing anything.

---

## ⚠️ Two things to fix before this goes public

### 1. The doctors' photographs are placeholders

`assets/photos/adway-kulkarni.jpg` and `vrushali-kulkarni.jpg` came from the
design mock-up. **They are AI-generated images of people who do not exist** —
one still carries a watermark in the corner.

Replace both with real photographs of the actual doctors. Keep the filenames, or
update the two `<img src="...">` lines in `index.html` to match.

Publishing an invented person under a named doctor's credentials is a
misrepresentation whatever the intent, and on a medical site it is worse than
most.

### 2. Check the domain

Every absolute address in the file — the canonical link, the sharing preview,
the structured data — says `https://cemhcare.com`. If the site will live
anywhere else, find and replace that one string throughout `index.html`,
`robots.txt` and `sitemap.xml`.

Getting this wrong does not break the page. It quietly tells Google the real
site is somewhere else.

---

## Publishing

The site is static, so almost anything will host it. Within Google Workspace:

**Google Sites** will not take these files. It is a page builder, not a host —
you would be rebuilding the page inside its editor and losing the layout, the
structured data and the sharing preview. Use one of the following instead.

**Firebase Hosting** (free tier, custom domain, HTTPS included) — the usual
choice for a site this size:

```
npm install -g firebase-tools
firebase login
firebase init hosting     # public directory: .   single-page app: No
firebase deploy
```

**Google Cloud Storage** — create a bucket named after the domain, upload the
files, set `index.html` as the main page, make the bucket public.

**Anywhere else** — Netlify, Cloudflare Pages, GitHub Pages and ordinary shared
hosting all take these files unchanged. Upload the folder; there is nothing to
compile.

After publishing, submit `https://<your-domain>/sitemap.xml` in
[Google Search Console](https://search.google.com/search-console).

---

## Making changes

Edit `index.html`. It is one file with the sections in the order they appear:
header, hero, about, doctors, services, why-us, FAQs, contact, footer.

**A doctor joins or leaves.** Copy an existing `<article class="wf-doctor">`
block and edit it. Then update the `"employee"` list in the JSON-LD block near
the top of the file — that is what Google reads, and a doctor in one place but
not the other is worse than a doctor in neither.

**Phone number, address or hours change.** They appear in more than one place —
the header, the contact card, the footer and the JSON-LD. Search the file for
the old value and change every one.

**Colours.** All of them are named at the top of `assets/css/website.css`.

---

## The appointment form

There isn't one, on purpose. The page offers a telephone number and a WhatsApp
link, both of which reach a person immediately.

A static page cannot receive a form on its own, and a form that silently goes
nowhere is worse than no form at all — the visitor has been told the clinic will
ring, and nothing rings.

If you want one back, the simplest route is a Google Form: build it in Drive,
choose **Send → embed (`<>`)**, and paste the `<iframe>` where `index.html` says
to. There is a comment marking the exact spot in the contact section. Responses
land in a Google Sheet the clinic already has.

Whichever route you take, make sure somebody is actually watching where the
answers go.

---

## What is already handled

Title, description and canonical link; Open Graph and Twitter cards with a
sharing image; favicons; `robots.txt` and `sitemap.xml`; and schema.org
structured data describing the clinic, both doctors and the FAQs — which is what
turns a search result into a listing with opening hours and named consultants.

One `<h1>`, ordered headings, alt text on every image, keyboard-operable tabs and
FAQs, no webfonts and no external requests, so it loads quickly on mobile data.

**There are deliberately no ratings or reviews in the structured data.** There is
no real source for them, so any number would be invented — which is against
Google's policy, grounds for removal from search, and a lie told to somebody
choosing a doctor. Real reviews on the clinic's Google Business Profile appear in
search on their own and are the honest route to the same thing.
