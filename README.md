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

**3. Sorting is unauthenticated CPU.** nginx rate-limits `/api/sort` to 10/min per
address and the app caps concurrency at 2 with a 90s timeout, but a distributed
caller can still keep both workers busy. If the site gets attention, put it behind
CloudFront or Cloudflare.

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
