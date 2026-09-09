from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone

import requests
from backend.runtime import source_get, checkpoint, pause, record_failure, ScanCancelled
from backend.sources.structured import annotate
from backend.parsing import normalize_url, strip_html as _strip_html
from backend.config import GUPY_MAX_PER_QUERY

GUPY_BASE = "https://employability-portal.gupy.io/api/v1/jobs"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _make_id(url: str) -> str:
    norm = normalize_url(url)
    return hashlib.md5(norm.encode()).hexdigest()


def _parse_date(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None


def _build_location(job: dict) -> str:
    work_type = (job.get("workplaceType") or "").lower().replace("-", "_")
    city  = (job.get("city")  or "").strip()
    state = (job.get("state") or "").strip()

    if job.get("isRemoteWork") or work_type == "remote":
        return "Remoto"
    if work_type == "hybrid":
        return f"{city}, {state} (Híbrido)".strip(", ")
    return f"{city}, {state} (Presencial)".strip(", ")


def fetch_gupy_jobs(query: str, max_results: int = GUPY_MAX_PER_QUERY) -> list[dict]:
    results: list[dict] = []
    seen_urls: set[str] = set()
    seen_pages = set()
    offset = 0
    page_size = 10

    while len(results) < max_results:
        checkpoint()
        try:
            r = source_get(
                GUPY_BASE,
                params={"jobName": query, "limit": page_size, "offset": offset},
                headers=HEADERS,
                timeout=12,
            )
            if r.status_code != 200:
                print(f"[Gupy] HTTP {r.status_code} para '{query}' offset={offset}")
                break

            data = r.json()
            batch = data.get("data", [])
            total_available = data.get("pagination", {}).get("total", 0)

            if not batch:
                break
            page_urls = tuple(j.get("jobUrl") for j in batch)
            if page_urls in seen_pages:
                print(f"[Gupy] Página repetida para '{query}'; encerrando paginação.")
                break
            seen_pages.add(page_urls)

            for j in batch:
                checkpoint()
                job_url = (j.get("jobUrl") or "").strip()
                if not job_url or not job_url.startswith("http"):
                    continue
                if "inactive" in job_url.lower():
                    continue

                norm_url = normalize_url(job_url)
                if norm_url in seen_urls:
                    continue

                if j.get("type") == "vacancy_type_talent_pool":
                    continue

                pub_raw = j.get("publishedDate") or ""
                pub_dt = _parse_date(pub_raw)
                seen_urls.add(norm_url)
                results.append({
                    "id":          _make_id(norm_url),
                    "title":       (j.get("name") or "").strip(),
                    "company":     (j.get("careerPageName") or "").strip() or "Não informada",
                    "url":         norm_url,
                    "source":      "Gupy",
                    "location":    _build_location(j),
                    "posted_date": pub_dt.date().isoformat() if pub_dt else None,
                    "application_deadline": j.get("applicationDeadline"),
                    "description": _strip_html(j.get("description") or ""),
                })

            offset += page_size
            pause(0.3)

            if offset >= total_available:
                break

        except requests.exceptions.Timeout:
            print(f"[Gupy] Timeout para '{query}' offset={offset}")
            break
        except ScanCancelled:
            raise
        except Exception as e:
            record_failure(e)
            print(f"[Gupy] Erro: {e}")
            break

    return [annotate(job, "api") for job in results[:max_results]]


def deduplicate(candidates: list[dict]) -> list[dict]:
    seen_ids:  set[str] = set()
    seen_urls: set[str] = set()
    out = []
    for c in candidates:
        norm_url = normalize_url(c.get("url", ""))
        c_id = c.get("id") or _make_id(norm_url)
        c["id"] = c_id
        c["url"] = norm_url

        if c_id in seen_ids or norm_url in seen_urls:
            continue
        seen_ids.add(c_id)
        seen_urls.add(norm_url)
        out.append(c)
    return out
