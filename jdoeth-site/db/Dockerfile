# A "sqlite3 container" cannot be a database server, because SQLite has no server
# process to run. What it can usefully be is the volume owner, schema applier and
# backup job for the database file, plus a shell for ad-hoc queries.
FROM alpine:3.21

RUN apk add --no-cache sqlite tzdata

COPY schema.sql /schema.sql
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

HEALTHCHECK --interval=5s --timeout=3s --start-period=2s --retries=12 \
  CMD test -f /data/.ready || exit 1

VOLUME ["/data"]
ENTRYPOINT ["/entrypoint.sh"]
