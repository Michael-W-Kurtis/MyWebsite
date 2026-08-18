# jdoeth-site

A personal site with a working front end for [satyarth/pixelsort](https://github.com/satyarth/pixelsort).
Runs as a three-container Compose stack on Ubuntu; designed to move to AWS without
a rewrite.

```
browser ──▶ nginx :80 ──▶ web :8000 (FastAPI + uvicorn)
                │                     │
                │ serves /static      │ spawns `python -m pixelsort`
                │ /uploads /results   │
                └────────┬────────────┘
                    sitedata volume
                    (SQLite + images)
                         │
                    db container
              (owns the volume, applies
               schema, hourly-ish backups)
```

---

## Quick start

```bash
git clone <this repo> && cd jdoeth-site
cp .env.example .env          # optional; every value has a default
docker compose up -d --build
```

Open **http://localhost:8080**. First build takes about a minute; after that,
startup is a few seconds.

```bash
docker compose logs -f web    # follow the app
docker compose down           # stop, keep data
docker compose down -v        # stop, delete the volume (uploads, stats, backups)
```

### Without Docker

```bash
cd web
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DATA_DIR=./data uvicorn app.main:app --reload --port 8000
```

http://localhost:8000. Everything works except nginx's static serving and rate
limiting — the app falls back to serving those paths itself.

---

## Administrator tasks

### Replace the resume

Overwrite this file with the real resume, in Markdown:

| | |
|---|---|
| **Repository path** | `web/content/resume.md` |
| **Path in container** | `/app/content/resume.md` |

The directory is bind-mounted **read-only** into the container, so an edit on the
host is live on the next page load. No rebuild, no restart.

Optionally drop a PDF at `web/content/resume.pdf` — filename exactly that — and a
"Download PDF" button appears on the page automatically.

### Replace the photos

| Purpose | Path | Notes |
|---|---|---|
| Portrait | `web/app/static/img/portrait.jpg` | 4:5 portrait crop. Anything else is cropped to fit. |
| Hero background | `web/app/static/img/landscape.jpg` | Wide landscape, ≥1600px. |

Both are currently generated placeholders. These live in the image, so:
`docker compose up -d --build web`.

The "Pixelsort Me!" results are cached against the portrait's modification time,
so swapping the file invalidates the cache automatically.

### Add a project

Append one dict to `catalogue` in `web/app/routes/pages.py`. The grid picks it up.
If it needs a page of its own, add a route and a template beside `pixelsort.html`.

### Add a Unity WebGL game

Make a folder under `web/content/games/` named for the game, put the Unity output
in a `build/` subfolder inside it, and reload. The folder name becomes the URL.

```
web/content/games/orbital-drift/
├── about.json      (optional: title, description, controls, resolution)
├── cover.png       (optional: thumbnail)
└── build/          (required: the Unity output, copied in unchanged)
    ├── UnityLoader.js
    ├── orbital.json
    └── orbital.*.unityweb
```

That is live at `/games/orbital-drift`, and a card appears on `/projects`
automatically. That directory is bind-mounted read-only, so **no rebuild and no
restart** — which matters, because Unity builds are large.

Both Unity generations are supported and detected automatically: `UnityLoader.js`
(5.6 through 2019.x) and `<name>.loader.js` (2020+). You don't need the original
`index.html` — this site renders its own page around the canvas.

**Full instructions, including the exact file list for each Unity version and a
troubleshooting table, are in `web/content/games/README.md`** — deliberately kept
next to where the files go.

### Tune the batch grids

Every ladder, axis assignment and sweep range lives in one file:
`web/app/pixelsort_spec.py`'s sibling, **`web/app/batch_spec.py`**. Edit it and
rebuild, or — to change them without a rebuild — drop a JSON file at
`web/content/batch_ladders.json`. That directory is bind-mounted, so a restart
picks it up. Only the keys you supply are overridden:

```json
{
  "ladders": { "clength": { "4": [5, 25, 90, 400] } },
  "matrix":  { "row_values": { "4": ["random", "waves", "edges", "none"] } }
}
```

Two things in that file are easy to break by accident, and both are commented
in place: `randomness` ladders run **descending** (it is the percentage of
intervals *not* sorted, so raising it does less), and the `edges` and `threshold`
ladders deliberately stop short of the range's end because the effect saturates
there — spacing them evenly would waste two of four columns on near-duplicates.

### Query the database

```bash
docker compose exec db sqlite3 /data/db/site.db
sqlite> SELECT ip, visit_count, last_seen FROM visitors ORDER BY last_seen DESC LIMIT 20;
sqlite> SELECT status, COUNT(*), AVG(duration_ms) FROM sort_jobs GROUP BY status;
```

Backups land in `/data/db/backups/` as gzipped `.backup` snapshots, taken daily,
seven kept. Restore is a copy:

```bash
docker compose stop web
docker compose exec db sh -c "gunzip -c /data/db/backups/site-<STAMP>.db.gz > /data/db/site.db"
docker compose start web
```

---

## The two pixelsort apps

| | `/projects/pixelsort` | `/projects/pixelsort-batch` |
|---|---|---|
| Job | Dial in one image precisely | Find out what you want |
| Input cap | `MAX_IMAGE_DIM` 1600 px | `BATCH_MAX_EDGE` 3200 px, or `0` for none |
| Output | One render | 9 or 16 renders, each at the input's own size |
| Feels like | Seconds | Tens of seconds to minutes |

The batch app has three grid modes:

- **Matrix** — every interval function crossed with every sorting function. The
  widest view of the range, and where to start if you don't know what you want.
- **Intensity** — lock one interval and sorting function; two parameters then ramp
  so the effect grows toward the bottom right. Top left is the subtlest render.
- **Sweep** — one parameter stepped across every cell, for dialling in a number.

Three implementation notes worth knowing before changing anything:

**Renders are full size; the grid displays derivatives.** Each cell writes a
full-resolution PNG *and* a 900 px WebP. The grid points at the WebP, because
sixteen 6.4 MP images is 410 MB of decoded bitmap and kills the tab. This is not
the same as sorting small — the PNG is a genuine full-resolution sort, and the
derivative is that same image viewed smaller. Downloads and the lightbox always
serve the PNG.

**There is no job queue, deliberately.** `runner.result_path_for()` names outputs
by a hash of parameters plus input, so a finished cell already sits on disk under a
deterministic name and re-requesting it is a cache hit. Reload the page, or come
back to its `?batch=` URL later, and completed cells reappear instantly while the
rest resume. A job table would have bought nothing else.

**`random` and `waves` are not reproducible.** pixelsort seeds nothing, so those
interval functions build a fresh layout on every run and cells using them are not a
perfectly controlled comparison. The UI says so on affected grids rather than hiding
it. Seeding would mean importing pixelsort in-process instead of running the CLI,
which would break the rule that the command shown is the command run.

---

## Why the app serves Unity builds instead of nginx

Unity's `.unityweb` files are compressed on disk. The browser only unpacks them if
the server sends `Content-Encoding: gzip` or `br`, and getting that wrong is the
single most common reason a Unity WebGL build hangs partway through its progress
bar — the loader either fails outright or falls back to a slow JavaScript
decompressor and complains that the web server is misconfigured.

nginx cannot tell gzip from brotli by looking at a filename, and both ship as
`.unityweb`. The app can: it reads the first two bytes. gzip has magic bytes
(`1f 8b`); brotli has none, so a `.unityweb` that is neither gzip nor a
recognisable uncompressed Unity payload is treated as brotli. An explicit
`"compression"` key in `about.json` overrides the guess.

The cost is that a 40 MB payload passes through Python rather than straight off
disk. For a personal site that is a good trade — nginx still streams it through
with `proxy_buffering off`, so nothing is staged to a temp file on the way past.
If you would rather nginx serve them directly and you know your build is gzip,
add a `location` with `add_header Content-Encoding gzip` and point it at the
volume.

---

## Before you expose this to the internet

Three things that are fine on localhost and not fine on a public IP.

**1. The stats page publishes visitor IP addresses.** That is what the brief asked
for, and it is what it does — but an IP address is personal data in the EU and UK,
and this page hands every visitor's address to every other visitor. Two switches,
either one in `.env`:

```
STATS_MASK_IPS=true       # 203.0.113.47 becomes 203.0.113.x
STATS_TOKEN=<long-random> # /stats then requires ?token=<long-random>
```

**2. There is no TLS.** nginx listens on plain HTTP. Terminate TLS at a load
balancer, or add certbot and a `listen 443 ssl` block.

**3. Sorting is unauthenticated CPU, and the batch app is 16x that.** nginx
rate-limits `/api/sort` to 10/min, `/api/batch` to 4/min and cell fetches to
120/min, and each app has its own concurrency semaphore so neither can starve the
other. It is still the most expensive thing on the site by an order of magnitude.
**`/api/batch` is the first endpoint to put behind auth** if this ever faces real
traffic, and `BATCH_MAX_EDGE` is the single number that bounds the damage.

`TRUSTED_PROXIES` should stay narrow. It lists the source ranges allowed to set
`X-Real-IP`. Widen it to `0.0.0.0/0` and any client can forge its own address into
your stats table.

---

## Design notes

### Why `python -m pixelsort` as a subprocess

pixelsort installs **no console script** — `python -m pixelsort` is the only CLI
entry point, so anything shelling out to a bare `pixelsort` fails. It could be
imported as a library instead, and that would be marginally faster, but then the
command shown above the SORT button would be a *description* of what happened
rather than the thing that ran. The subprocess also gives a hard kill on a runaway
job, which an in-process call does not.

### Why uploads are downscaled

`pixelsort` places pixels in a pure-Python loop. Measured on one modern core:

| Size | Time |
|---|---|
| 600 × 400 | 0.95 s |
| 1200 × 800 | 4.4 s |
| 1600 × 1200 | 8.4 s |
| 4000 × 3000 | ~50 s (extrapolated) |

Roughly 4 µs per pixel, linear in pixel count. `MAX_IMAGE_DIM=1600` keeps the
worst case near 8 s. Raise it and raise `JOB_TIMEOUT_SECONDS` with it.

Every upload is decoded, downscaled and re-encoded as PNG from the decoded pixel
buffer. That one step drops EXIF payloads, polyglot files and mislabelled content
types, and caps the cost of the sort.

### Why the controls appear and disappear

Every interval function in pixelsort takes `**kwargs` and reads only what it needs.
Passing `-t` to `random` is silently accepted and does nothing — which is worse than
an error, because the UI would look like it was doing something. So the wrapper
hides irrelevant controls rather than disabling them, and drops them from the
command:

| Interval function | Reads |
|---|---|
| `random`, `waves` | `clength` |
| `threshold` | `lower_threshold`, `upper_threshold` |
| `edges` | `lower_threshold` |
| `file` | interval image |
| `file-edges` | interval image, `lower_threshold` |
| `none` | nothing |

`angle`, `randomness` and `sorting_function` always apply. This table lives once,
in `web/app/pixelsort_spec.py`, and is serialised into the page — so the browser's
command preview and the server's executed command are built from the same rules and
cannot drift.

### Why the "sqlite3 container" is a sidecar

SQLite is a library. There is no server process, so a SQLite container cannot be a
database service and nothing can connect to it over the network. What it can
usefully be, and what `db/` is, is the volume's owner and caretaker:

1. **It owns the permissions.** A fresh named volume is created root-owned; the web
   container runs as uid 10001. Something privileged has to prepare the directories
   first. `web` waits on the db container's healthcheck, not merely its start, so it
   never boots against an unwritable `/data`.
2. **It applies the schema**, so the database is valid whichever service starts first.
3. **It takes hot backups** with `.backup`, which is safe against a live writer.
4. **It is the operator's shell** for ad-hoc queries.

---

## Moving to AWS

The stack is deliberately ordinary, so the migration is mostly deletion.

**Smallest step — lift and shift.** One EC2 instance (t3.small is enough), Docker
and Compose, `git pull && docker compose up -d --build`. Put an ALB in front for
TLS. The `sitedata` volume becomes an EBS volume. Nothing in the code changes.

**Next step — ECS Fargate.** Push `web` and `nginx` to ECR. The two things that
need real decisions:

- **Drop the `db` container.** Its job was owning a local volume. On Fargate,
  replace SQLite with **RDS Postgres or Aurora Serverless v2**, and swap `app/db.py`
  for SQLAlchemy or `psycopg`. The rest of the app does not care — every query is
  already isolated in that one module.
- **Move images to S3.** `UploadedImages/` and `results/` become a bucket, served
  through CloudFront. Uploads go direct-to-S3 with presigned URLs; results are
  written back by the worker.

**Do not put the SQLite file on EFS.** This is the constraint that decides when you
outgrow SQLite — not a traffic number. SQLite's locking depends on POSIX advisory
locks that NFS does not implement reliably, and WAL mode requires shared memory
that NFS cannot provide at all. It will corrupt. Either keep the file on a single
instance's EBS volume, or move to Postgres.

**Sorting is CPU-bound**, so if it gets popular, pull it out of the request path:
SQS queue, a worker service, and poll for the result. The API contract in
`routes/api.py` already returns a result URL, so the front end needs one polling
loop and nothing else.

---

## Assumptions and configurable details

Called out because I chose these, you didn't:

| Choice | Reasoning | Change it |
|---|---|---|
| FastAPI + Jinja + vanilla JS | pixelsort is Python, so the app should be too. No build step means no toolchain to maintain for a personal site. | — |
| Host port `8080` | 80 usually needs root and is often taken. | `HTTP_PORT` |
| System font stacks | No CDN dependency, no layout shift. Verdana/Trebuchet on the wrapper page is a choice, not a fallback: it *is* the 2001 web. | `--sans` / `--face` in the CSS |
| Uploads deleted after 24 h | They are disposable and the volume would otherwise grow forever. Portrait renders are exempt. | `RETENTION_HOURS` |
| Stats count pages, not requests | Static assets and API calls would drown the numbers. | `TRACKED_PATHS` in `app/tracking.py` |
| Canned portrait sort | `-i threshold -t 0.20 -u 0.85`, with angle and sorting function exposed. A wide band smears without dissolving a face. | `routes/api.py` |
| Interval images auto-resized | pixelsort requires an exact size match and errors out otherwise. Nearest-neighbour, to preserve hard black/white edges. | `routes/api.py` |
| Resume is Markdown | Easier to keep current than HTML, and prints cleanly. | — |
| Placeholder identity | "Portland, OR", the blurb, and the resume are invented. Nothing in the code depends on them. | `.env`, `resume.md`, `templates/index.html` |

### Verified (games)

Both loader generations detected and started in a real browser — legacy
`UnityLoader.instantiate` and modern `createUnityInstance`; `Content-Encoding`
set correctly per file from magic bytes, and confirmed meaningful by removing the
header and watching the loader fail exactly as a misconfigured server would;
`.wasm.br` served as `application/wasm` rather than as a brotli blob; path
traversal out of a game directory refused; builds with a missing loader or a
missing `build/` directory rendering an actionable message instead of a dead
progress bar; and the game download gated behind a Play button so opening the
page does not pull tens of megabytes.

### Verified (batch app)

Plan generation across all three modes and both grid sizes; every generated cell
producing a command the CLI accepts; the corner-first fill order; nine- and
sixteen-cell batches rendering end to end in a browser; cache hits on re-request;
resume after a full page reload restoring all completed cells; the zip containing
full-size PNGs plus a `commands.txt`; the lightbox, copy-command and keyboard
navigation; cells taking the source's aspect ratio so nothing is cropped; and the
measured effect ladders confirmed monotonic in the direction the UI claims.

### Verified

Route sweep (all 200/302/404 as expected); portrait sort and its cache; upload sort
across `threshold`, `waves`, `none`, `file`; rejection of non-images, missing
interval files, inverted thresholds and unknown function names; a 4200×3100 upload
downscaling to 1600×1181 rather than being refused; visit tracking excluding static
assets; and a browser pass over both pages at 1440px and 390px confirming the
conditional controls match the table above, the command preview matches the
executed command exactly, and there are no console errors.

**Not verified:** the Compose stack itself has not been built — Docker was
unavailable in the environment where this was written. The application, templates,
CSS and JS were all exercised against a live server; the container wiring is
reviewed but unrun. Expect the first `docker compose up --build` to be where any
remaining issue surfaces.
