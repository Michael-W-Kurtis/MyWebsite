#!/bin/sh
# ---------------------------------------------------------------------------
# SQLite maintenance sidecar.
#
# SQLite is a library, not a server, so there is nothing here to "connect" to.
# This container is the operator's console and backup job for the database file.
# It has three jobs:
#
#   1. Own the volume layout. A fresh named volume is created root-owned, and
#      the web container runs as uid 10001, so something privileged has to make
#      the directories writable first. This runs as root; the web app does not.
#   2. Apply the schema, so the database is valid whether or not the app booted.
#   3. Take a consistent hot backup on a fixed interval with .backup, which is
#      safe to run while the app is writing.
#
# Ad-hoc queries:  docker compose exec db sqlite3 /data/db/site.db
# ---------------------------------------------------------------------------
set -eu

DB=/data/db/site.db
BACKUP_DIR=/data/db/backups
APP_UID=${APP_UID:-10001}
APP_GID=${APP_GID:-10001}
INTERVAL=${BACKUP_INTERVAL_SECONDS:-86400}
KEEP=${BACKUP_KEEP:-7}

echo "[db] preparing /data for uid ${APP_UID}"
mkdir -p /data/UploadedImages /data/results /data/batches /data/db "$BACKUP_DIR"

echo "[db] applying schema to $DB"
sqlite3 "$DB" < /schema.sql

# Chown after creating the db file so the WAL and SHM siblings inherit correctly.
chown -R "${APP_UID}:${APP_GID}" /data
chmod -R u+rwX,g+rwX /data

# The healthcheck waits on this, so the web container never starts against an
# unwritable volume.
touch /data/.ready
chown "${APP_UID}:${APP_GID}" /data/.ready

echo "[db] ready. backup every ${INTERVAL}s, keeping ${KEEP}"

while true; do
    sleep "$INTERVAL"
    STAMP=$(date -u +%Y%m%dT%H%M%SZ)
    TARGET="$BACKUP_DIR/site-$STAMP.db"
    if sqlite3 "$DB" ".backup '$TARGET'"; then
        gzip -f "$TARGET"
        echo "[db] backup written: $TARGET.gz"
    else
        echo "[db] backup FAILED at $STAMP" >&2
    fi
    ls -1t "$BACKUP_DIR"/site-*.db.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
done
