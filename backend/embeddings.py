"""Pré-ranqueamento semântico de vagas por similaridade ao currículo.

Antes de gastar o orçamento caro do LLM (SCAN_LIMIT vagas por scan), ordena os
candidatos por similaridade de cosseno entre o embedding da vaga e o do currículo.
Assim as vagas mais aderentes são pontuadas primeiro.

Usa a API de embeddings do Gemini (batchEmbedContents). Degrada graciosamente:
qualquer falha (sem chave, offline, erro de API) devolve a ordem original.
"""
from __future__ import annotations  # compat: `X | None` como type hint no Python 3.9

import math
import time

import requests

from backend.runtime import checkpoint, pause, ScanCancelled
from backend.config import GEMINI_API_KEY, GEMINI_EMBED_MODEL
from backend.prompt import TARGET_PROFILE

_EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"
_BATCH_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
_BATCH_SIZE = 25  # lotes menores para evitar estourar quota por minuto
_TIMEOUT = 30


def _model_path() -> str:
    return f"models/{GEMINI_EMBED_MODEL}"


def _embed_one(text: str) -> list[float] | None:
    try:
        r = requests.post(
            _EMBED_URL.format(model=GEMINI_EMBED_MODEL),
            params={"key": GEMINI_API_KEY},
            json={"model": _model_path(), "content": {"parts": [{"text": text}]}},
            timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            print(f"[Embed] HTTP {r.status_code}: {r.text[:200]}")
            return None
        return r.json().get("embedding", {}).get("values")
    except ScanCancelled:
        raise
    except Exception as e:
        print(f"[Embed] Erro ao embeddar currículo: {e}")
        return None


def _embed_batch(texts: list[str]) -> list[list[float] | None]:
    """Embeda uma lista de textos; retorna vetores na MESMA ordem (None por falha)."""
    out: list[list[float] | None] = []
    for start in range(0, len(texts), _BATCH_SIZE):
        checkpoint()
        chunk = texts[start:start + _BATCH_SIZE]
        body = {
            "requests": [
                {"model": _model_path(), "content": {"parts": [{"text": t}]}}
                for t in chunk
            ]
        }
        try:
            r = requests.post(
                _BATCH_URL.format(model=GEMINI_EMBED_MODEL),
                params={"key": GEMINI_API_KEY},
                json=body,
                timeout=_TIMEOUT,
            )
            if r.status_code != 200:
                print(f"[Embed] HTTP {r.status_code} no lote: {r.text[:200]}")
                out.extend([None] * len(chunk))
                continue
            embeddings = r.json().get("embeddings", [])
            for i in range(len(chunk)):
                vals = embeddings[i].get("values") if i < len(embeddings) else None
                out.append(vals)
            pause(0.4)
        except ScanCancelled:
            raise
        except Exception as e:
            print(f"[Embed] Erro no lote: {e}")
            out.extend([None] * len(chunk))
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _candidate_text(c: dict) -> str:
    title = (c.get("title") or "").strip()
    desc = (c.get("description") or "").strip()
    return f"{title}\n{desc}"[:2000]


def rank_by_similarity(candidates: list[dict], reference: str = TARGET_PROFILE) -> list[dict]:
    """Ordena os candidatos (desc) por similaridade ao perfil da vaga IDEAL.

    Usa TARGET_PROFILE (o que o candidato quer) em vez do currículo completo, para
    não inflar vagas de Dados/BI. Em caso de falha, devolve a lista inalterada.
    """
    if not GEMINI_API_KEY or len(candidates) <= 1:
        return candidates

    resume_vec = _embed_one(reference)
    if resume_vec is None:
        return candidates

    job_vecs = _embed_batch([_candidate_text(c) for c in candidates])
    if all(v is None for v in job_vecs):
        return candidates

    # Candidatos sem vetor vão para o fim (similaridade -1).
    scored = [
        (_cosine(resume_vec, v) if v is not None else -1.0, idx, c)
        for idx, (c, v) in enumerate(zip(candidates, job_vecs))
    ]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [c for _, _, c in scored]
