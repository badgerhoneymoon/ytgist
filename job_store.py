"""Durable, single-engine jobs. SQLite transactions arbitrate submit/cancel/claim.

Only queued work is recovered automatically. Running work after a crash is marked
interrupted: a retry must be an explicit request, never hidden GPU work.
SSE readers observe revisions; they do not consume each other's events.
"""
import json
import fcntl
import os
import sqlite3
import threading
import time
import uuid

TERMINAL = frozenset({"succeeded", "failed", "cancelled", "interrupted"})


class Conflict(ValueError):
    pass


class JobStore:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.owner = open(path + ".lock", "a")
        try:
            fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.owner.close()
            raise RuntimeError("Another engine owns this job database.") from None
        self.changed = threading.Condition(threading.RLock())
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, request TEXT NOT NULL,
            status TEXT NOT NULL, created REAL NOT NULL,
            updated REAL NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
            event TEXT NOT NULL, result TEXT, error TEXT)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS submission_keys (
            key TEXT PRIMARY KEY, job TEXT NOT NULL REFERENCES jobs(id))""")
        with self.db:
            self.db.execute("""UPDATE jobs SET status='interrupted',
                error='Engine stopped during processing; submit again to retry.',
                event=?, updated=?, revision=revision+1
                WHERE status IN ('running', 'cancelling')""",
                (json.dumps({"error": "Engine stopped during processing; submit again to retry."}),
                 time.time()))

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        d = dict(row)
        for key in ("request", "event", "result"):
            d[key] = json.loads(d[key]) if d[key] is not None else None
        return d

    def get(self, job):
        with self.changed:
            return self._decode(self.db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone())

    def list(self, limit=50):
        with self.changed:
            rows = self.db.execute("SELECT * FROM jobs ORDER BY created DESC, rowid DESC LIMIT ?",
                                   (limit,)).fetchall()
            return [self._decode(r) for r in rows]

    def submit(self, kind, request, idem=None):
        encoded = json.dumps(request, sort_keys=True, separators=(",", ":"))
        with self.changed, self.db:
            if idem:
                old = self.db.execute("""SELECT jobs.* FROM jobs JOIN submission_keys
                    ON jobs.id=submission_keys.job WHERE submission_keys.key=?""", (idem,)).fetchone()
                if old:
                    if old["kind"] != kind or old["request"] != encoded:
                        raise Conflict("Idempotency-Key was already used for a different request.")
                    return self._decode(old), False
            # Identical active requests share work, including UI and agent submissions.
            old = self.db.execute("""SELECT * FROM jobs WHERE kind=? AND request=?
                AND status IN ('queued', 'running') ORDER BY created LIMIT 1""",
                (kind, encoded)).fetchone()
            if old:
                if idem:
                    self.db.execute("INSERT INTO submission_keys VALUES (?,?)", (idem, old["id"]))
                return self._decode(old), False
            job, now = uuid.uuid4().hex, time.time()
            event = {"stage": "check", "pct": 0, "msg": "queued — waiting for the engine"}
            self.db.execute("""INSERT INTO jobs
                (id,kind,request,status,created,updated,event) VALUES (?,?,?,?,?,?,?)""",
                (job, kind, encoded, "queued", now, now, json.dumps(event)))
            if idem:
                self.db.execute("INSERT INTO submission_keys VALUES (?,?)", (idem, job))
            self.changed.notify_all()
            return self.get(job), True

    def claim(self):
        with self.changed, self.db:
            row = self.db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created, rowid LIMIT 1").fetchone()
            if row is None:
                return None
            self.db.execute("UPDATE jobs SET status='running', updated=?, revision=revision+1 WHERE id=?",
                            (time.time(), row["id"]))
            self.changed.notify_all()
            return self.get(row["id"])

    def emit(self, job, event):
        with self.changed, self.db:
            old = self.get(job)
            if not old or old["status"] in TERMINAL:
                return
            status, result, error = old["status"], None, None
            if event.get("stopped"):
                status = "cancelled"
            elif "error" in event:
                status, error = "failed", event["error"]
            elif "markdown" in event or event.get("frames_done"):
                status, result = "succeeded", event
            # A cancellation accepted before completion wins the race.
            if old["status"] == "cancelling" and status in TERMINAL:
                status, result, error, event = "cancelled", None, None, {"stopped": True}
            elif status not in TERMINAL:
                # Reconnecting readers need the cumulative ETA and phase, not just
                # the last delta. GPU series are deliberately not replayed repeatedly.
                event = {**{key: value for key, value in old["event"].items()
                            if key in ("stage", "pct", "msg", "eta", "video_minutes")},
                         **event}
            self.db.execute("""UPDATE jobs SET status=?,event=?,result=?,error=?,
                updated=?,revision=revision+1 WHERE id=?""",
                (status, json.dumps(event), json.dumps(result) if result is not None else None,
                 error, time.time(), job))
            self.changed.notify_all()

    def cancel(self, job):
        with self.changed, self.db:
            old = self.get(job)
            if not old:
                return False
            if old["status"] in TERMINAL:
                return old["status"] == "cancelled"
            status = "cancelled" if old["status"] == "queued" else "cancelling"
            event = {"stopped": True} if status == "cancelled" else old["event"]
            self.db.execute("UPDATE jobs SET status=?,event=?,updated=?,revision=revision+1 WHERE id=?",
                            (status, json.dumps(event), time.time(), job))
            self.changed.notify_all()
            return True

    def wait(self, job, revision, timeout=15):
        with self.changed:
            self.changed.wait_for(lambda: self.get(job)["revision"] > revision
                                  or self.get(job)["status"] in TERMINAL, timeout=timeout)
            return self.get(job)

    def close(self):
        with self.changed:
            self.db.close()
            self.owner.close()
