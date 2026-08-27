#!/usr/bin/env bash
# Hot backup of the SQLite database. Safe to run while the site is serving:
# .backup takes a consistent snapshot without blocking writers.
#
#   0 4 * * *  /srv/jdoeth-site/deploy/backup-db.sh >> /var/log/jdoeth-backup.log 2>&1
#
# The docker compose stack already does this in the db sidecar. This is for the
# systemd/no-Docker deployment.
set -euo pipefail

DB="${DB:-/srv/jdoeth-site-data/db/site.db}"
DEST="${DEST:-/srv/jdoeth-site-data/db/backups}"
KEEP="${KEEP:-14}"

mkdir -p "$DEST"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
sqlite3 "$DB" ".backup '$DEST/site-$STAMP.db'"
gzip -f "$DEST/site-$STAMP.db"
echo "[$(date -uIs)] wrote $DEST/site-$STAMP.db.gz"

ls -1t "$DEST"/site-*.db.gz | tail -n +$((KEEP + 1)) | xargs -r rm -f
