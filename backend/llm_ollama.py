from __future__ import annotations  # compat: `X | None` como type hint no Python 3.9

import json
import re
import requests
from backend.runtime import checkpoint, pause, ScanCancelled
from backend.config import OLLAMA_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT
from backend.prompt import build_match_prompt, REQUIRED_FIELDS
from backend.scoring import finalize_score

_THINK_TAG = re.compile(r"<think>.*?</think>", re.DOTALL)


def _resilient_json_parse(raw_str: str) -> dict | None:
    # Remove thinking tags and extract content
    cleaned = _THINK_TAG.sub("", raw_str).strip()
    
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start < 0 or end <= start:
        print(f"[Ollama JSON Parser] Delimitadores {{}} não encontrados na resposta.")
        return None
        
    json_str = cleaned[start:end]
    
    # Try 1: Standard JSON parse
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass
        
    # Try 2: Clean trailing commas before closing braces/brackets
    # e.g., { "foo": "bar", } -> { "foo": "bar" }
    json_str_cleaned = re.sub(r',\s*([\]}])', r'\1', json_str)
    try:
        return json.loads(json_str_cleaned)
    except json.JSONDecodeError:
        pass

    # Try 3: Replace raw physical newlines inside string values with '\n'
    # Raw newlines inside JSON strings are technically invalid JSON.
    try:
        # Regex to locate multiline strings and replace raw newlines with escaped '\n'
        # Simple character traversal to replace control characters but maintain structure
        escaped_str = []
        in_string = False
        escape_next = False
        for char in json_str_cleaned:
            if char == '"' and not escape_next:
                in_string = not in_string
            if char == '\\' and not escape_next:
                escape_next = True
            else:
                escape_next = False
                
            if in_string and char == '\n':
                escaped_str.append('\\n')
            elif in_string and char == '\r':
                pass
            else:
                escaped_str.append(char)
                
        final_str = "".join(escaped_str)
        return json.loads(final_str)
    except json.JSONDecodeError as e:
        print(f"[Ollama JSON Parser] Falha ao decodificar JSON após limpeza avançada: {e}")
        print(f"[Ollama JSON Parser] JSON problemático:\n{json_str}")
        
    return None


def score_job(title: str, company: str, desc: str) -> dict | None:
    checkpoint()
    prompt = build_match_prompt(title, company, desc[:3000] if desc else "(sem descrição disponível)")

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_thread": 2},
    }

    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=OLLAMA_TIMEOUT,
        )

        if r.status_code != 200:
            print(f"[Ollama] HTTP {r.status_code}: {r.text[:200]}")
            return None

        result = _resilient_json_parse(r.json().get("response", ""))

        if not result or not REQUIRED_FIELDS.issubset(result.keys()):
            print(f"[Ollama] JSON incompleto ou inválido: {result}")
            return None

        return finalize_score(result)

    except requests.exceptions.Timeout:
        print(f"[Ollama] Timeout após {OLLAMA_TIMEOUT}s para '{title}'")
        return None
    except json.JSONDecodeError as e:
        print(f"[Ollama] Erro ao parsear JSON: {e}")
        return None
    except ScanCancelled:
        raise
    except Exception as e:
        print(f"[Ollama] Erro inesperado: {e}")
        return None
