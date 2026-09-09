"""Programathor: listagem e detalhes públicos, sem inventar campos ausentes."""
import hashlib
import re
from urllib.parse import quote_plus, urljoin
import requests
from backend.parsing import normalize_url, strip_html
from backend.runtime import source_get, checkpoint, pause, record_failure, ScanCancelled
from backend.sources.structured import annotate, extract_job, job_postings

_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept-Language": "pt-BR,pt;q=0.9"}
_TIMEOUT = (5, 15)


def normalize_programathor_url(url):
    return normalize_url(urljoin("https://programathor.com.br", url)) if url else ""


def _make_id(url):
    return hashlib.md5(normalize_programathor_url(url).encode()).hexdigest()


def _icon_text(html, icon):
    match = re.search(r"<i[^>]*class=['\"][^'\"]*" + icon + r"[^'\"]*['\"][^>]*></i>(.*?)</span>", html, re.S | re.I)
    return strip_html(match.group(1)) if match else None


def parse_listing(html):
    pattern = r'<a\b[^>]*href=[\"\'](/jobs/\d+[^\"\']*)[\"\'][^>]*>(.*?)</a>'
    for href, inner in re.findall(pattern, html, re.S | re.I):
        title = re.search(r'<h3[^>]*>(.*?)</h3>', inner, re.S | re.I)
        if not title:
            continue
        yield annotate({
            "id": _make_id(href), "url": normalize_programathor_url(href), "source": "Programathor",
            "title": strip_html(title.group(1)), "company": _icon_text(inner, "fa-briefcase"),
            "location": _icon_text(inner, "fa-map-marker-alt"), "posted_date": None, "description": "",
        }, "listagem")


def fetch_programathor_jobs(query, max_results=15):
    results = []
    checkpoint()
    try:
        response = source_get(f"https://programathor.com.br/jobs?q={quote_plus(query)}", headers=_HEADERS, timeout=_TIMEOUT)
        seen = set()
        for job in parse_listing(response.text):
            checkpoint()
            if len(results) >= max_results:
                break
            if job["url"] in seen:
                continue
            seen.add(job["url"])
            try:
                detail = source_get(job["url"], headers=_HEADERS, timeout=_TIMEOUT)
                posting = next(job_postings(detail.text), None)
                if posting:
                    extracted = extract_job(posting)
                    quality = dict(job["data_quality"])
                    for key, value in extracted.items():
                        if value:
                            job[key] = value
                            quality[key] = "json_ld"
                    annotate(job, "listagem", quality)
                else:
                    record_failure("Detalhes sem JobPosting estruturado; dados limitados à listagem.")
            except ScanCancelled:
                raise
            except Exception as error:
                record_failure(error)
            # A busca do site pode ignorar q: confirme o termo nos dados coletados.
            terms = [word.casefold() for word in query.split() if len(word) > 2]
            text = (job["title"] + " " + job["description"]).casefold()
            if not terms or any(word in text for word in terms):
                results.append(job)
            pause(0.2)
    except ScanCancelled:
        raise
    except Exception as error:
        record_failure(error)
    return results
