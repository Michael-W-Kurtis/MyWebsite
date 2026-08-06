-- Applied by the db container on boot. The web app applies the identical schema
-- in its own lifespan hook, so either can start first. Everything is IF NOT EXISTS.
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS visitors (
    ip           TEXT PRIMARY KEY,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    visit_count  INTEGER NOT NULL DEFAULT 0,
    last_path    TEXT,
    last_agent   TEXT
);

CREATE TABLE IF NOT EXISTS visits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ip         TEXT NOT NULL,
    path       TEXT NOT NULL,
    user_agent TEXT,
    ts         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_visits_ts ON visits(ts);
CREATE INDEX IF NOT EXISTS idx_visits_ip ON visits(ip);

CREATE TABLE IF NOT EXISTS sort_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ip           TEXT,
    source       TEXT NOT NULL,
    command      TEXT NOT NULL,
    duration_ms  INTEGER,
    status       TEXT NOT NULL,
    ts           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_ts ON sort_jobs(ts);
