"""Provider de scoring via Google Gemini (API generativelanguage).

Usa saída estruturada (responseSchema + responseMimeType=application/json).
Quando atinge rate limit (429) ou sobrecarga (503), falha rápido para permitir
que o fallback Groq em backend/llm.py assuma imediatamente.
"""
from __future__ import annotations

import json
import requests

from backend.runtime import checkpoint, pause, ScanCancelled
from backend.config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_TIMEOUT
from backend.prompt import build_match_prompt, RESPONSE_SCHEMA, REQUIRED_FIELDS
from backend.scoring import finalize_score

_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)
_RETRYABLE = {429, 503}

_FALLBACK_MODELS = list(dict.fromkeys([
    GEMINI_MODEL,
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-flash-latest",
]))


def _post_with_retry(payload: dict, label: str) -> requests.Response | None:
    for model_name in _FALLBACK_MODELS:
        checkpoint()
        url = _ENDPOINT.format(model=model_name)
        try:
            r = requests.post(
                url,
                params={"key": GEMINI_API_KEY},
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=GEMINI_TIMEOUT,
            )
        except requests.exceptions.Timeout:
            print(f"[Gemini] Timeout ({model_name}) para '{label}'")
            continue
        except ScanCancelled:
            raise
        except Exception as e:
            print(f"[Gemini] Erro de rede ({model_name}): {e}")
            continue

        if r.status_code == 200:
            return r

        if r.status_code in _RETRYABLE:
            print(f"[Gemini] HTTP {r.status_code} ({model_name}) para '{label}'")
            # Se for 429 (cota esgotada na chave), não adianta tentar outros modelos da mesma API
            if r.status_code == 429:
                return None
            continue

        print(f"[Gemini] HTTP {r.status_code} ({model_name}): {r.text[:160]}")

    return None


def score_job(title: str, company: str, desc: str) -> dict | None:
    if not GEMINI_API_KEY:
        return None

    prompt = build_match_prompt(title, company, desc[:6000] if desc else "(sem descrição disponível)")

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }

    r = _post_with_retry(payload, title)
    if r is None:
        return None

    try:
        data = r.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return None

        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            return None

        result = json.loads(text)

        if not isinstance(result, dict) or not REQUIRED_FIELDS.issubset(result.keys()):
            return None

        return finalize_score(result)

    except ScanCancelled:
        raise
    except Exception as e:
        print(f"[Gemini] Erro ao processar resposta: {e}")
        return None
