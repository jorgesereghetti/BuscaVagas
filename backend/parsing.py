"""Normalização compartilhada de textos HTML e URLs das fontes."""

import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


class _HTMLTextExtractor(HTMLParser):
    """Extrai texto puro de HTML, virando quebras de linha em tags de bloco."""

    _LINE_BREAK_TAGS = {"br", "p", "li", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "br":
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._LINE_BREAK_TAGS:
            self._parts.append("\n")

    def get_text(self) -> str:
        return "".join(self._parts)


def strip_html(text: str) -> str:
    """Remove tags HTML, decodifica entidades e normaliza espaços/linhas em branco."""
    if not text:
        return ""

    parser = _HTMLTextExtractor()
    parser.feed(text)
    extracted = unescape(parser.get_text())

    # Substitui nbsp residual por espaço comum
    extracted = extracted.replace("\xa0", " ")

    # Colapsa espaços/tabs (sem afetar quebras de linha)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in extracted.splitlines()]

    # Remove linhas em branco excessivas
    cleaned: list[str] = []
    for line in lines:
        if not line and (not cleaned or not cleaned[-1]):
            continue
        cleaned.append(line)

    return "\n".join(cleaned).strip()


def normalize_url(url: str) -> str:
    """Remove apenas tracking conhecido; preserva parâmetros que identificam vagas."""
    if not isinstance(url, str) or not url.strip():
        return ""
    try:
        p = urlparse(url.strip())
        if p.scheme.lower() not in ("http", "https") or not p.hostname:
            return ""
        tracking = {"jobboardsource", "source", "ref", "refid", "trackingid", "trk", "trkinfo", "fbclid", "gclid"}
        query = [(key, value) for key, value in parse_qsl(p.query, keep_blank_values=True)
                 if not key.lower().startswith("utm_") and key.lower() not in tracking]
        host = p.netloc.lower()
        path = p.path.rstrip("/")
        if p.hostname.lower() == "linkedin.com" or p.hostname.lower().endswith(".linkedin.com"):
            match = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)$", path)
            if match:
                host, path, query = "www.linkedin.com", f"/jobs/view/{match.group(1)}", []
        return urlunparse((p.scheme.lower(), host, path, p.params, urlencode(sorted(query)), ""))
    except ValueError:
        return ""


def source_identity(source: str, url: str) -> str:
    """Identidade estável no domínio da fonte, sem juntar títulos iguais."""
    normalized = normalize_url(url)
    return f"{(source or '').casefold()}:{normalized}" if normalized else ""
