# Photographs for the public website

Drop a doctor's photograph in this folder and the website picks it up. Nothing
else needs changing — no code, no admin screen, no restart in production beyond
the usual `collectstatic`.

## Naming

Name the file after the doctor, lower case, with a hyphen instead of the space:

```
photos/vrushali-kulkarni.jpg
photos/adway-kulkarni.jpg
```

The username is tried too, so `photos/dr-vrushali.jpg` also works if that is
what you have. `.jpg`, `.jpeg`, `.png` and `.webp` are all read; if two files
match the same doctor, the `.jpg` wins.

A doctor with no matching file is **not** broken on the page — they appear with
their initials in a tinted box. Adding the photograph later is enough to replace
it.

## What makes a good one

The page crops to a tall portrait, 120px wide, and on a phone it becomes a
full-width band cropped near the top. So:

- **Portrait orientation**, face in the upper third.
- Roughly **900px wide or more**. Smaller looks soft on a modern screen.
- Under about **300KB** each. These load on mobile data outside a hospital.

## ⚠️ The two files here now are placeholders

`adway-kulkarni.jpg` and `vrushali-kulkarni.jpg` were taken from the design
mock-up. **They are AI-generated images of people who do not exist** — one of
them still carries a watermark in the corner.

They are here so the layout can be seen working, and they must be replaced with
real photographs of the actual doctors before this site is published. A clinic
page showing an invented person under a named doctor's credentials is a
misrepresentation, whatever the intent.

Delete a placeholder and the page falls back to initials, which is a truthful
thing to show while waiting for the real photograph.
