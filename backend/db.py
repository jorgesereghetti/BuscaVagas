"""Persistência SQLite, migração versionada e deduplicação em lote."""
import json
import os
import sqlite3
from contextlib import contextmanager
from backend.parsing import normalize_url as normalize_db_url, source_identity
from backend.policy import STATUS_ALIASES, is_expired
from backend import config

DB_PATH = os.environ.get("BUSCAVAGAS_DB", os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'vagas.db')))
_SUBSCORE_COLUMNS = ("domain_score", "skills_score", "seniority_score", "location_score")
_EXTRA_COLUMNS = ("strengths", "gaps", "data_quality", "workplace", "normalized_url", "source_key", "application_deadline")


def _conn():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def connection(write=False):
    conn = _conn()
    try:
        if write:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        if write:
            conn.commit()
    except Exception:
        if write:
            conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Migra dentro de uma transação; nenhuma vaga é apagada ou fundida."""
    with connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    with connection(write=True) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, title TEXT, company TEXT, url TEXT UNIQUE,
            source TEXT, location TEXT, description TEXT, posted_date TEXT,
            match_score INTEGER, match_tier TEXT, rationale TEXT, pitch TEXT,
            status TEXT DEFAULT 'nova', date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        for column in _SUBSCORE_COLUMNS + _EXTRA_COLUMNS:
            if column not in existing:
                kind = "INTEGER" if column in _SUBSCORE_COLUMNS else "TEXT"
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {kind}")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            for old, canonical in STATUS_ALIASES.items():
                if old != canonical:
                    conn.execute("UPDATE jobs SET status=? WHERE status=?", (canonical, old))
            for row in conn.execute("SELECT id, url, source FROM jobs").fetchall():
                conn.execute("UPDATE jobs SET normalized_url=?, source_key=? WHERE id=?",
                             (normalize_db_url(row["url"]), source_identity(row["source"], row["url"]), row["id"]))
            # Valores presumidos do coletor antigo não são tratados como fatos.
            conn.execute("""UPDATE jobs SET location=NULL, posted_date=NULL, company=NULL,
                         workplace='nao_informado', data_quality='{}'
                         WHERE source='Programathor' AND company='Tech Company'
                           AND location='Remoto / São Paulo'""")
            conn.execute("PRAGMA user_version=1")
        if version < 2:
            # O produto passou a ter somente três estados canônicos. Estados de
            # triagem voltam para Novas; entrevistas pertencem às Enviadas.
            conn.execute("UPDATE jobs SET status='nova' WHERE status IN ('new','analisada','analyzed','selecionada','selected')")
            conn.execute("UPDATE jobs SET status='enviada' WHERE status IN ('applied','entrevista','interviewing')")
            conn.execute("UPDATE jobs SET status='oculta' WHERE status IN ('ignored','rejected','nao_compativel','incompativel')")
            conn.execute("PRAGMA user_version=2")
        for column in ("status", "posted_date", "date_added", "normalized_url", "source_key"):
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_jobs_{column} ON jobs({column})")
        conn.execute("DROP INDEX IF EXISTS idx_jobs_url")
        conn.execute("""CREATE TABLE IF NOT EXISTS scan_runs (
            id TEXT PRIMARY KEY, state TEXT NOT NULL, cancel_requested INTEGER DEFAULT 0,
            started_at REAL NOT NULL, heartbeat REAL NOT NULL, finished_at REAL,
            progress_text TEXT, current INTEGER DEFAULT 0, total INTEGER DEFAULT 0,
            result TEXT DEFAULT '{}', error TEXT
        )""")
        conn.execute("DROP INDEX IF EXISTS idx_one_active_scan")
        conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_scan
                     ON scan_runs((1)) WHERE state IN ('queued','running')""")


def _find_evaluations(conn, candidates):
    matches = {}
    for start in range(0, len(candidates), 200):
        chunk = candidates[start:start + 200]
        ids = [job.get("id", "") for job in chunk]
        urls = [normalize_db_url(job.get("url")) for job in chunk]
        keys = [source_identity(job.get("source"), job.get("url")) for job in chunk]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(f"""SELECT id, url, normalized_url, source_key, rationale, status
            FROM jobs WHERE id IN ({placeholders}) OR normalized_url IN ({placeholders})
                OR source_key IN ({placeholders})
            ORDER BY CASE WHEN status IN ('enviada','applied','entrevista','interviewing') THEN 0 ELSE 1 END, date_added""",
            ids + urls + keys).fetchall()
        by_id, by_url, by_key = {}, {}, {}
        for row in rows:
            record = dict(row)
            by_id.setdefault(row["id"], record)
            if row["normalized_url"]:
                by_url.setdefault(row["normalized_url"], record)
            if row["source_key"]:
                by_key.setdefault(row["source_key"], record)
        for job, url, key in zip(chunk, urls, keys):
            found = by_id.get(job.get("id")) or by_key.get(key) or by_url.get(url)
            if found:
                matches[job.get("id")] = found
    return matches


def get_job_evaluations(candidates):
    with connection() as conn:
        return _find_evaluations(conn, candidates)


def get_job_evaluation(job_id, url=None, title=None, company=None):
    # Argumentos antigos preservados; título/empresa não são identidade da vaga.
    return get_job_evaluations([{"id": job_id, "url": url}]).get(job_id)


def save_match(job, score, tier, rationale, pitch="", status="nova", subscores=None, strengths=None, gaps=None):
    """Retorna True quando grava; preserva decisões do usuário em conflitos."""
    canonical_status = STATUS_ALIASES.get(status)
    if canonical_status is None:
        raise ValueError("Status inválido")
    url = normalize_db_url(job.get("url"))
    if not job.get("id") or not url:
        raise ValueError("Vaga sem identidade ou URL válida")
    subscores = subscores or {}
    fields = {
        **{key: job.get(key) for key in ("id", "title", "company", "source", "location", "description", "posted_date", "workplace", "application_deadline")},
        "url": url, "normalized_url": url, "source_key": source_identity(job.get("source"), url),
        "match_score": score, "match_tier": tier, "rationale": rationale, "pitch": pitch or "", "status": canonical_status,
        **{key: subscores.get(key) for key in _SUBSCORE_COLUMNS},
        "strengths": json.dumps(strengths or [], ensure_ascii=False),
        "gaps": json.dumps(gaps or [], ensure_ascii=False),
        "data_quality": json.dumps(job.get("data_quality") or {}, ensure_ascii=False),
    }
    with connection(write=True) as conn:
        existing = _find_evaluations(conn, [job]).get(job["id"])
        if existing:
            if not ((existing["rationale"] or "").startswith("LLM falhou") and existing["status"] in ("oculta", "ignored")):
                return False
            fields.pop("id")
            # URL original fica estável; a identidade normalizada permite novos aliases.
            fields.pop("url")
            assignments = ",".join(f"{key}=?" for key in fields)
            conn.execute(f"UPDATE jobs SET {assignments} WHERE id=?", list(fields.values()) + [existing["id"]])
        else:
            names = ",".join(fields)
            placeholders = ",".join("?" for _ in fields)
            conn.execute(f"INSERT INTO jobs ({names}) VALUES ({placeholders})", list(fields.values()))
    return True


def update_job_status(job_id, new_status):
    canonical = STATUS_ALIASES.get(new_status)
    if canonical is None:
        raise ValueError("Status inválido")
    with connection(write=True) as conn:
        return conn.execute("UPDATE jobs SET status=? WHERE id=?", (canonical, job_id)).rowcount > 0


def discard_job(job_id):
    return update_job_status(job_id, "oculta")


def ignore_all_new_jobs():
    with connection(write=True) as conn:
        conn.execute("UPDATE jobs SET status='oculta' WHERE status IN ('new','nova')")


def get_jobs(status=None, tier=None, include_rejected=False):
    conditions, params = [], []
    if not include_rejected:
        conditions.append("match_tier != 'REJECTED'")
    if status:
        canonical = STATUS_ALIASES.get(status, status)
        aliases = [key for key, value in STATUS_ALIASES.items() if value == canonical] or [canonical]
        conditions.append("status IN (" + ",".join("?" for _ in aliases) + ")")
        params.extend(aliases)
    if tier:
        conditions.append("match_tier=?")
        params.append(tier)
    query = "SELECT * FROM jobs" + (" WHERE " + " AND ".join(conditions) if conditions else "")
    with connection() as conn:
        rows = conn.execute(query + " ORDER BY match_score DESC, date_added DESC", params).fetchall()
    result = []
    for row in rows:
        job = dict(row)
        for field, expected in (("strengths", list), ("gaps", list), ("data_quality", dict)):
            try:
                value = json.loads(job.get(field) or "null")
                job[field] = value if isinstance(value, expected) else expected()
            except (TypeError, ValueError):
                job[field] = expected()
        result.append(job)
    return result


def purge_jobs():
    with connection(write=True) as conn:
        return conn.execute("DELETE FROM jobs WHERE status NOT IN ('applied','interviewing','entrevista','enviada')").rowcount


def get_stats():
    with connection() as conn:
        row = conn.execute("""SELECT SUM(match_tier != 'REJECTED') AS total_jobs,
            SUM(match_tier='HIGH' AND status NOT IN ('ignored','oculta')) AS high_matches,
            SUM(status IN ('applied','enviada','interviewing','entrevista')) AS applied,
            SUM(status IN ('interviewing','entrevista')) AS interviewing FROM jobs""").fetchone()
    return {key: row[key] or 0 for key in row.keys()}


def cleanup_old_jobs():
    policy = config.runtime_snapshot()["policy"]
    with connection(write=True) as conn:
        expired = [(row["id"],) for row in conn.execute("SELECT * FROM jobs WHERE status IN ('new','nova')")
                   if is_expired(dict(row), policy)]
        conn.executemany("UPDATE jobs SET status='oculta' WHERE id=?", expired)
    return len(expired)
