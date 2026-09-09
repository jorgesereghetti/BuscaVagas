"""Seleção de provedores com checkpoints entre todas as tentativas."""
from backend.config import LLM_PROVIDER, GEMINI_API_KEY, GROQ_API_KEY
from backend.runtime import checkpoint
from backend.llm_gemini import score_job as _gemini_score
from backend.llm_groq import score_job as _groq_score
from backend.llm_ollama import score_job as _ollama_score


def score_job(title, company, desc):
    providers = {
        'gemini': _gemini_score if GEMINI_API_KEY else None,
        'groq': _groq_score if GROQ_API_KEY else None,
        'ollama': _ollama_score,
    }
    order = list(dict.fromkeys([LLM_PROVIDER, 'gemini', 'groq', 'ollama']))
    for name in order:
        checkpoint()
        provider = providers.get(name)
        if provider is None:
            continue
        result = provider(title, company, desc)
        checkpoint()
        if result is not None:
            return result
    return None
