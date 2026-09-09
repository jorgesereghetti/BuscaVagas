"""Fonte Remotar Jobs (vagas 100% remotas e tecnologia no Brasil).

Coleta vagas remotas brasileiras com suporte a RSS e HTML scraper resiliente.
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import quote_plus

import requests
from backend.runtime import source_get, checkpoint, pause, record_failure, ScanCancelled
from backend.sources.structured import annotate
from backend.parsing import normalize_url as normalize_remotar_url, strip_html as _strip_html

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/xml,text/xml,application/json,text/html,*/*",
    "Accept-Language": "pt-BR,pt;q=0.9",
}
_TIMEOUT = 15


def _make_id(url: str) -> str:
    norm = normalize_remotar_url(url)
    return hashlib.md5(norm.encode()).hexdigest()


def fetch_remotar_jobs(query: str, max_results: int = 15) -> list[dict]:
    """Busca vagas no Remotar filtrando por termos-chave."""
    results: list[dict] = []
    feed_urls = [
        f"https://remotar.com.br/feed/?s={quote_plus(query)}",
        "https://remotar.com.br/feed/",
    ]

    seen_urls: set[str] = set()

    for url in feed_urls:
        checkpoint()
        if len(results) >= max_results:
            break
        try:
            resp = source_get(url, headers=_HEADERS, timeout=_TIMEOUT)
            if resp.status_code != 200:
                continue

            root = ET.fromstring(resp.content)
            channel = root.find("channel")
            if channel is None:
                continue

            for item in channel.findall("item"):
                checkpoint()
                if len(results) >= max_results:
                    break

                title_el = item.find("title")
                link_el = item.find("link")
                desc_el = item.find("description")
                pub_date_el = item.find("pubDate")

                if title_el is None or link_el is None:
                    continue

                raw_title = title_el.text or ""
                job_url = normalize_remotar_url(link_el.text or "")
                if not job_url or job_url in seen_urls:
                    continue

                seen_urls.add(job_url)

                company = None
                title = raw_title
                if " na " in raw_title:
                    parts = raw_title.split(" na ", 1)
                    title, company = parts[0].strip(), parts[1].strip()
                elif " - " in raw_title:
                    parts = raw_title.split(" - ", 1)
                    title, company = parts[0].strip(), parts[1].strip()

                desc_text = _strip_html(desc_el.text or "") if desc_el is not None else ""

                q_words = [w.lower() for w in query.split() if len(w) > 2]
                text_to_check = (title + " " + desc_text).lower()
                if q_words and not any(w in text_to_check for w in q_words):
                    continue

                posted_date = None
                if pub_date_el is not None and pub_date_el.text:
                    try:
                        from email.utils import parsedate_to_datetime
                        dt = parsedate_to_datetime(pub_date_el.text.strip())
                        posted_date = dt.strftime("%Y-%m-%d")
                    except Exception:
                        try:
                            dt = datetime.strptime(pub_date_el.text[:16], "%a, %d %b %Y")
                            posted_date = dt.strftime("%Y-%m-%d")
                        except Exception:
                            pass

                job_item = {
                    "id": _make_id(job_url),
                    "title": title,
                    "company": company,
                    "url": job_url,
                    "source": "Remotar",
                    "location": "Remoto",
                    "posted_date": posted_date,
                    "description": desc_text[:4000],
                }
                results.append(job_item)

        except ScanCancelled:
            raise
        except Exception as error:
            record_failure(error)
            continue

    return [annotate(job, "rss", {"location": "portal_remoto"}) for job in results]
