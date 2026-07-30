# Clinic Patient Management System

Patient management for an adult & paediatric endocrinology clinic — bookings,
the daily reception queue, the doctor's patient chart, prescriptions and billing.

Django 5.2 · PostgreSQL · server-rendered templates + HTMX · no JavaScript build step.

---

## What works today

| Area | State |
|---|---|
| Custom user model with doctor / receptionist / patient roles | Done |
| Single login page, role-based routing to the right dashboard | Done |
| Patients with a clinic-unique UHID | Done |
| Visit lifecycle with enforced state transitions | Models done, reception screens pending |
| **Doctor's patient chart** — summary, notes, investigations, growth chart, prescriptions | Done |
| Growth charts against published percentile references | Done |
| Append-only audit trail of patient-record access | Done |
| Reception queue screens, online booking, billing screens | Models only |

## Running it locally

### With Docker (nothing else to install)

```bash
docker compose up --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py seed_demo
```

### Against a local PostgreSQL

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements/dev.txt

createdb clinic_pms
export DATABASE_URL=postgres://USER:PASSWORD@localhost:5432/clinic_pms

python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Then open <http://127.0.0.1:8000/login/>.

`seed_demo` creates five patients with several years of visits, lab results and
growth measurements. Every demo account uses the password `clinicdemo2026`:

| Username | Role |
|---|---|
| `vrushali` | Doctor (paediatric cases) |
| `adway` | Doctor (adult cases) |
| `reception` | Receptionist |

The command refuses to run with `DEBUG` off unless given `--force`, so it cannot
be pointed at the clinic's live database by accident.

## Tests

```bash
pytest
```

The growth-reference suite checks the percentile maths against the SD columns
published in the reference tables themselves, so it proves the code reproduces
the official values rather than merely agreeing with itself.

## Layout

```
config/          settings (base/dev/prod/test), URLs, clinic customisation
accounts/        custom User, roles, login, role-based permissions
patients/        Patient, UHID allocation, standing history
appointments/    Visit and its state machine
clinical/        notes, investigations, diagnoses
growth/          anthropometry + growth charts       ← optional app
pharmacy/        prescriptions
billing/         charges, payments, receipts
audit/           append-only access log
portal/          role dashboards (doctor chart lives here)
```

## Customising this for another clinic

Each clinic runs its own deployment, with its own database, domain and look.
A clinic-specific copy should differ in **these four places only**:

| What | Where |
|---|---|
| Colours, fonts, spacing | `static/css/theme.css` — CSS variables only |
| Name, address, UHID prefix, enabled features | `config/clinic.py` (or environment variables) |
| Logo and letterhead | `templates/branding/` |
| Which speciality features exist | `OPTIONAL_APPS` in `config/clinic.py` |

Keeping the differences confined to those four is what lets a clinic copy keep
merging fixes from this repository instead of drifting into an unmaintainable
fork. **Resist changing anything else per clinic.**

### Speciality features are removable apps

`growth` is the first example. Drop it from `OPTIONAL_APPS` and the models,
admin and the dashboard's Growth Chart tab all disappear together — an
orthopaedic clinic never sees it. Nothing in the core apps imports from
`growth`; that is what keeps removal clean, and it is the pattern any future
speciality feature should follow.

## Growth reference data — read before clinical use

> Percentile curves are drawn from **WHO (0–5 years)** and **CDC (2–20 years)**
> published tables. The WHO 5–19 year reference and the **IAP 2015** Indian
> charts are **not** included and must be sourced separately.
>
> Which standard to chart Indian children against is a clinical decision for
> the treating doctor, not a technical default. See
> [`growth/reference/SOURCES.md`](growth/reference/SOURCES.md).

## Deployment

Built to run as a container behind Caddy, which obtains TLS certificates
automatically. The database is deliberately **not** in the compose file for
production — it should be a managed PostgreSQL instance with automated backups
and point-in-time recovery, because losing patient history is the one failure
this clinic cannot recover from.

```bash
cp .env.example .env        # fill in SECRET_KEY, DATABASE_URL, ALLOWED_HOSTS, domain
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec web python manage.py migrate
docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
```

Production settings refuse to start if `SECRET_KEY` is still the development
default or `ALLOWED_HOSTS` is empty.

### Handling patient data

Health records, so the defaults are strict and should stay that way:

- HTTPS enforced, HSTS enabled, secure and HTTP-only cookies
- Sessions expire after 30 minutes of inactivity
- Django admin is mounted at an unguessable path from `ADMIN_URL`; `/admin/` is a decoy
- Role checks are enforced in views, never only by hiding links in templates
- Every patient-record view is written to the audit log, which cannot be edited or deleted
- Application logs must never contain patient identifiers
