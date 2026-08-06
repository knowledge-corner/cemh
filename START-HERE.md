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

### Having it start on its own

If you would rather not click anything at all, double-click **AUTOSTART-ON.bat**
once. After that, switching the computer on starts the clinic system and opens
it in your browser by itself.

Two things to know:

* In Docker Desktop, go to **Settings** and tick **"Start Docker Desktop when
  you log in"**. Without it there is nothing for the automatic start to start.
* It waits up to two minutes for Docker to wake up, because Docker Desktop is
  not ready the instant you log in.

To stop it happening, double-click **AUTOSTART-OFF.bat**. Nothing else changes,
and no patient records are affected.

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

## Using it from a phone or tablet

Anything on the **same Wi-Fi** as this computer can open the system. There is
nothing to install on the phone — it is the same screens, in the phone's own
browser.

**1. Find this computer's address on the network.**

| Your computer | Do this |
|---|---|
| Windows | Press the Windows key, type `cmd`, press Enter. Type `ipconfig` and press Enter. Read **IPv4 Address** — something like `192.168.1.7`. |
| Mac | Apple menu → **System Settings** → **Network** → click the connected Wi-Fi. The address is shown there. |

**2. On the phone, open the browser and go to that address with `:8000` after
it** — for example `http://192.168.1.7:8000`.

Type it into the **address bar**, not the search box, or the phone will look it
up on Google instead of finding it on your own network.

Worth knowing:

* **This computer must be switched on with the system running.** The phone is
  only a window onto it. Nothing is stored on the phone.
* **The address can change.** Most routers hand out a new one every so often, so
  if it stops working, run `ipconfig` again — it is almost always that. If it
  becomes a nuisance, ask whoever set up the router for a "reserved address" or
  "static lease" for this computer.
* **Same Wi-Fi only.** It will not work on mobile data, or from home. That is
  deliberate.
* **Anyone else on that Wi-Fi can reach it too**, and needs only a username and
  password to get in. On the surgery's own network that is the point of it. Do
  not do this on a guest or shared network, and once the clinic depends on the
  system, stop handing the Wi-Fi password out freely.

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
computer, reachable from that computer and from anything on the same Wi-Fi —
see *Using it from a phone or tablet* above. That is a local network, not the
internet: nothing outside the surgery can reach it. Putting it online safely is
a separate job involving a domain name, a certificate and India-resident
hosting.

---

## Getting a new version

**There is nothing to do.** Start Docker Desktop and double-click
**START-CLINIC.bat** (Windows) or **START-CLINIC.command** (Mac), exactly as
every other morning. The launcher fetches the latest version itself, rebuilds if
it needs to, and brings the database up to date before opening the browser.

A few things worth knowing about that:

* If the update changed the launcher itself, it starts again in a fresh window.
  That is expected — let it.
* If the internet is down, or the update cannot be fetched for any other reason,
  it says so and **carries on with the version already on the computer**. The
  clinic still opens. Yesterday's system running beats no system at all.
* It never merges. If somebody has edited files on this computer, the update
  stops rather than trying to combine the two, and says so. Send that window on
  rather than trying to fix it during clinic hours.

Skipping the launcher and simply leaving the system running will pick up neither
the new code nor the database change, and pages that use a new field will show
an error like

```
column accounts_doctorprofile.category does not exist
```

If you see that, it means the system is running new code against an old
database. Double-clicking the launcher fixes it.

---

## If the address does not open

Docker prints `Started` when it has *asked* the container to start, not when
the system is actually up. So the first thing to do is ask what is really
running:

```
docker compose ps
```

Look at the **STATUS** column for `cmeh-web-1`:

| It says | What it means | What to do |
|---|---|---|
| `Up` | Running normally | Open <http://localhost:8000> |
| `Restarting` | Starting, failing, and trying again | Read the log, below |
| `Exited` | Stopped | Read the log, below |

```
docker compose logs --tail 50 web
```

The startup prints three numbered steps. Whichever number it stops at is where
it failed, and the reason is on the line underneath.

### "Illegal option -" in the log

```
/app/docker/entrypoint.sh: 7: set: Illegal option -
```

The files were copied to this computer with Windows line endings, which the
Linux system inside the container cannot read. Repair the copy:

```
git add --renormalize .
git checkout -- .
docker compose up -d --build
```

`--build` matters here: the corrected startup file has to be rebuilt into the
container image.
