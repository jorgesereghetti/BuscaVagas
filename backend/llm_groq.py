"""Provider de scoring via Groq (API compatível com OpenAI, nuvem rápida).

Usado como FALLBACK automático quando o Gemini atinge rate limit (429) ou sobrecarga (503).
Interface idêntica: score_job(title, company, desc) -> dict | None.
"""
from __future__ import annotations

import json
import requests

from backend.runtime import checkpoint, pause, ScanCancelled
from backend.config import GROQ_API_KEY, GROQ_MODEL, GROQ_TIMEOUT
from backend.prompt import build_match_prompt, REQUIRED_FIELDS
from backend.scoring import finalize_score

_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

# Modelos para fallback dentro do próprio Groq caso o primário esteja em manutenção
_GROQ_FALLBACK_MODELS = list(dict.fromkeys([
    GROQ_MODEL,
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]))


def score_job(title: str, company: str, desc: str) -> dict | None:
    if not GROQ_API_KEY:
        return None

    prompt = build_match_prompt(title, company, desc[:6000] if desc else "(sem descrição disponível)")

    for model_name in _GROQ_FALLBACK_MODELS:
        checkpoint()
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }

        try:
            r = requests.post(
                _ENDPOINT,
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=GROQ_TIMEOUT,
            )
        except requests.exceptions.Timeout:
            print(f"[Groq] Timeout ({model_name}) para '{title}'")
            continue
        except ScanCancelled:
            raise
        except Exception as e:
            print(f"[Groq] Erro de rede ({model_name}): {e}")
            continue

        if r.status_code != 200:
            print(f"[Groq] HTTP {r.status_code} ({model_name}): {r.text[:160]}")
            continue

        try:
            data = r.json()
            text = data["choices"][0]["message"]["content"].strip()
            result = json.loads(text)

            if not isinstance(result, dict) or not REQUIRED_FIELDS.issubset(result.keys()):
                print(f"[Groq] JSON incompleto ({model_name}): {result}")
                continue

            return finalize_score(result)

        except ScanCancelled:
            raise
        except Exception as e:
            print(f"[Groq] Erro ao parsear resposta ({model_name}): {e}")
            continue

    return None
