# Starting the clinic system

You do **not** need to install Python, or type any commands. You need Docker
Desktop, and then you double-click one file.

---

## First time only — install Docker Desktop

1. Go to <https://www.docker.com/products/docker-desktop/>
2. Download the version for your computer (Windows or Mac) and install it.
3. Open **Docker Desktop** and leave it running. You will know it is ready when
   the whale icon — near the clock on Windows, in the menu bar on a Mac —
   stops moving.

While installing, tick **"Start Docker Desktop when you log in"** in its
settings. Then it is always ready and you never have to think about it again.

---

## Every day — start the system

Open the project folder and double-click:

| Your computer | Double-click this |
|---|---|
| Windows | **START-CLINIC.bat** |
| Mac | **START-CLINIC.command** |

A black window opens and tells you what it is doing. **The first time takes a
few minutes** — it is downloading and building everything. After that it takes
about ten seconds.

When it is ready your browser opens by itself at **http://localhost:8000**.

You can close the black window. The system keeps running.

### Signing in

| Who | Username | Password |
|---|---|---|
| Reception | `reception` | `clinicdemo2026` |
| Dr Vrushali Kulkarni | `vrushali` | `clinicdemo2026` |
| Dr Adway Kulkarni | `adway` | `clinicdemo2026` |
| Administrator | `admin` | `clinicadmin2026` |

**Change all of these before real patients are entered.** The administrator can
do it from the admin page — see below.

---

## Shutting down

Double-click **STOP-CLINIC.bat** (Windows) or **STOP-CLINIC.command** (Mac).

You do not have to stop it at the end of each day. Leaving it running is fine,
and it starts itself again when the computer restarts.

**Nothing is lost when you stop it.** Patient records are kept separately from
the part that gets stopped and started.

---

## If something goes wrong

The window tells you what is wrong rather than closing instantly. The two
common ones:

**"Docker Desktop is installed but not running."**
Open Docker Desktop from the Start menu or Applications, wait for the whale
icon to settle, then double-click the start file again.

**The browser says the page cannot be reached.**
Give it another minute and refresh — on the very first start it is still
building. If it still will not load, stop it and start it again.

**Anything else.** Do not close the black window. Send a photo of it to whoever
supports the system; the answer is almost always written in it.

---

## Using Docker Desktop instead of the files

If you would rather use buttons than double-click a file, Docker Desktop shows
the clinic system under **Containers**, listed as `clinic-pms`. The **▶ Start**
and **■ Stop** buttons there do the same thing. The start file is only a
shortcut that also opens your browser for you.

---

## Things worth knowing

**Where the records live.** In a Docker *volume* called `pgdata`, not in this
folder. That is why stopping, updating or even deleting the containers does not
lose anything. It also means: if somebody deletes that volume, the records are
gone. Ask for backups to be set up before the clinic depends on this.

**The demo patients.** A new, empty system loads five example patients so there
is something to look at. Once real patients exist, the demo data is **never**
loaded again — the system checks before touching anything.

To start with no demo patients at all, open `docker-compose.yml` in Notepad and
change `SEED_DEMO: "1"` to `SEED_DEMO: "0"` before the first start.

**Changing passwords.** Sign in as `admin`, then go to
<http://localhost:8000/clinic-admin/> and open **Users**. Note that address is
not `/admin/` — that is deliberate.

**This is not yet set up for use over the internet.** It runs on this one
computer, reachable at `localhost`. Putting it online safely is a separate job
involving a domain name, a certificate and India-resident hosting.
