"""Fonte LinkedIn Jobs (endpoint público 'jobs-guest', sem login).

Throttle entre requisições, descarte de erros, normalização de links e filtro de 7 dias.
"""
from __future__ import annotations

import hashlib
import re
import time
from html import unescape
from urllib.parse import urlparse, urlunparse

import requests
from backend.runtime import source_get, checkpoint, pause, record_failure, ScanCancelled
from backend.sources.structured import annotate

from backend.config import LOCATION_PREFS, LINKEDIN_LOCATION, LINKEDIN_MAX_PER_QUERY

_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
_DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "pt-BR,pt;q=0.9",
}
_PAGE_SIZE = 25
_TIMEOUT = 15

_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r'base-search-card__title">\s*(.*?)\s*</h3>', re.S)
_COMPANY_RE = re.compile(r'base-search-card__subtitle">\s*(.*?)\s*</h4>', re.S)
_LOCATION_RE = re.compile(r'job-search-card__location">\s*(.*?)\s*</span>', re.S)
_URL_RE = re.compile(r'href="(https://[a-z]{0,3}\.?linkedin\.com/jobs/view/[^"?]+)', re.I)
_DATE_RE = re.compile(r'datetime="([0-9]{4}-[0-9]{2}-[0-9]{2})"')
_DESC_RE = re.compile(r'show-more-less-html__markup[^>]*>(.*?)</div>', re.S)


def normalize_linkedin_url(url: str) -> str:
    """Extrai a URL limpa canônica do LinkedIn sem query params de rastreamento."""
    if not url:
        return ""
    url = url.strip()
    try:
        # Pega padrão https://www.linkedin.com/jobs/view/1234567890
        m = re.search(r'(https://[a-z0-9\.\-]*linkedin\.com/jobs/view/\d+)', url, re.I)
        if m:
            return m.group(1)
        p = urlparse(url)
        clean_path = p.path.rstrip("/")
        return urlunparse((p.scheme.lower(), p.netloc.lower(), clean_path, "", "", "")) or url
    except Exception:
        return url.split("?")[0].rstrip("/")


def _make_id(url: str) -> str:
    norm = normalize_linkedin_url(url)
    return hashlib.md5(norm.encode()).hexdigest()


def _clean(html_fragment: str) -> str:
    return unescape(_TAG_RE.sub(" ", html_fragment)).replace("\xa0", " ").strip()


def _job_id_from_url(url: str) -> str | None:
    m = re.search(r"view/(\d+)|-(\d+)(?:/|$)", url)
    if m:
        return m.group(1) or m.group(2)
    return None


def _passes_location(loc: str) -> bool:
    low = loc.lower()
    if any(p in low for p in LOCATION_PREFS):
        return True
    return low.strip() in ("brazil", "brasil")


def _fetch_description(job_id: str) -> str:
    try:
        r = source_get(_DETAIL_URL.format(job_id=job_id), headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code != 200:
            return ""
        m = _DESC_RE.search(r.text)
        return _clean(m.group(1)) if m else ""
    except ScanCancelled:
        raise
    except Exception as error:
        record_failure(error)
        return ""


def fetch_linkedin_jobs(query: str, max_results: int = LINKEDIN_MAX_PER_QUERY) -> list[dict]:
    results: list[dict] = []
    seen_urls: set[str] = set()
    seen_pages = set()
    start = 0

    while len(results) < max_results:
        checkpoint()
        try:
            r = source_get(
                _SEARCH_URL,
                params={"keywords": query, "location": LINKEDIN_LOCATION, "start": start},
                headers=_HEADERS,
                timeout=_TIMEOUT,
            )
        except ScanCancelled:
            raise
        except Exception as e:
            record_failure(e)
            print(f"[LinkedIn] Erro de rede para '{query}' start={start}: {e}")
            break

        if r.status_code != 200:
            print(f"[LinkedIn] HTTP {r.status_code} para '{query}' start={start}")
            break

        cards = re.split(r"<li[ >]", r.text)[1:]
        if not cards:
            break
        page_urls = tuple(_URL_RE.findall(r.text))
        if not page_urls or page_urls in seen_pages:
            break
        seen_pages.add(page_urls)

        for card in cards:
            checkpoint()
            url_m = _URL_RE.search(card)
            title_m = _TITLE_RE.search(card)
            if not url_m or not title_m:
                continue
            raw_url = url_m.group(1)
            norm_url = normalize_linkedin_url(raw_url)
            if norm_url in seen_urls:
                continue

            loc_m = _LOCATION_RE.search(card)
            location = _clean(loc_m.group(1)) if loc_m else ""
            # Local explicitamente incompatível deve ser descartado na fonte;
            # localização ausente segue para a política configurável da engine.
            if location and not _passes_location(location):
                continue
            comp_m = _COMPANY_RE.search(card)
            date_m = _DATE_RE.search(card)
            
            job_id = _job_id_from_url(norm_url)

            seen_urls.add(norm_url)
            results.append({
                "id":          _make_id(norm_url),
                "title":       _clean(title_m.group(1)),
                "company":     _clean(comp_m.group(1)) if comp_m else None,
                "url":         norm_url,
                "source":      "LinkedIn",
                "location":    location or None,
                "posted_date": date_m.group(1) if date_m else None,
                "description": _fetch_description(job_id) if job_id else "",
            })
            pause(0.3)
            if len(results) >= max_results:
                break

        start += _PAGE_SIZE
        pause(0.5)

    return [annotate(job, "pagina") for job in results[:max_results]]
