"""Extração de JobPosting JSON-LD e evidência dos campos coletados."""
import json
from html.parser import HTMLParser
from backend.parsing import strip_html
from backend.policy import parse_date, workplace


class StructuredDataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts, self.parts, self.active = [], [], False

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self.active, self.parts = True, []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.scripts.append("".join(self.parts))
            self.active = False


def job_postings(html):
    parser = StructuredDataParser()
    parser.feed(html)
    def walk(value):
        if isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, dict):
            types = value.get("@type", [])
            if "JobPosting" in (types if isinstance(types, list) else [types]):
                yield value
            for key in ("@graph", "itemListElement", "item", "mainEntity"):
                if key in value:
                    yield from walk(value[key])
    for script in parser.scripts:
        try:
            # Alguns portais publicam quebras de linha literais dentro de
            # strings JSON-LD; o modo permissivo preserva esse conteúdo.
            yield from walk(json.loads(script, strict=False))
        except (TypeError, ValueError):
            continue


def extract_job(posting):
    organization = posting.get("hiringOrganization") or {}
    company = organization.get("name") if isinstance(organization, dict) else None
    places = posting.get("jobLocation") or []
    places = places if isinstance(places, list) else [places]
    locations = []
    for place in places:
        address = place.get("address", {}) if isinstance(place, dict) else {}
        if isinstance(address, dict):
            parts = [address.get(key) for key in ("addressLocality", "addressRegion", "addressCountry")]
            parts = [part.get("name") if isinstance(part, dict) else part for part in parts]
            text = ", ".join(part for part in parts if isinstance(part, str) and part)
            if text:
                locations.append(text)
        elif isinstance(address, str):
            locations.append(address)
    location = " / ".join(locations) or None
    if str(posting.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        location = "Remoto" + (f" · {location}" if location else "")
    date = parse_date(posting.get("datePosted"))
    return {
        "title": strip_html(posting.get("title") or ""),
        "company": company, "location": location,
        "posted_date": date.date().isoformat() if date else None,
        "application_deadline": posting.get("validThrough"),
        "description": strip_html(posting.get("description") or ""),
    }


def annotate(job, origin, evidence=None):
    quality = dict(evidence or {})
    for field in ("title", "company", "location", "posted_date", "description"):
        quality.setdefault(field, origin if job.get(field) else "ausente")
    job["data_quality"] = quality
    job["workplace"] = workplace(job.get("location"))
    return job
