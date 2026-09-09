"""Matemática determinística de score do matching currículo↔vaga.

O LLM devolve 4 sub-notas (domain/skills/seniority/location) em 0–100. Aqui as
combinamos por peso fixo, aplicamos o gate de validade e derivamos match_score +
match_tier de forma 100% determinística — sem depender do julgamento do modelo.
"""

WEIGHTS = {
    "domain_score": 0.50,
    "skills_score": 0.25,
    "seniority_score": 0.15,
    "location_score": 0.10,
}

# A engine oculta vagas inválidas ou com score abaixo de 50.
REJECTED_MAX_SCORE = 19

# Faixas de tier (limite inferior inclusivo).
TIER_HIGH = 75
TIER_MEDIUM = 50
TIER_LOW = 20


def _clamp_score(value) -> int:
    """Clampa uma sub-nota em 0..100 de forma defensiva (tolera lixo do LLM)."""
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _tier_for(score: int) -> str:
    if score >= TIER_HIGH:
        return "HIGH"
    if score >= TIER_MEDIUM:
        return "MEDIUM"
    if score >= TIER_LOW:
        return "LOW"
    return "REJECTED"


def finalize_score(result: dict) -> dict:
    """Combina as sub-notas em match_score/match_tier e injeta no dict.

    Mantém os demais campos intactos e retorna o próprio dict mutado.
    """
    scores = {field: _clamp_score(result.get(field)) for field in WEIGHTS}
    for field, value in scores.items():
        result[field] = value

    raw = round(sum(scores[field] * weight for field, weight in WEIGHTS.items()))

    validity = result.get("is_valid_grade", False)
    is_valid = validity is True or (isinstance(validity, str) and validity.strip().lower() == "true")
    result["is_valid_grade"] = is_valid
    rationale = result.get("rationale")
    result["rationale"] = rationale.strip() if isinstance(rationale, str) else ""
    if not is_valid:
        result["match_score"] = min(raw, REJECTED_MAX_SCORE)
        result["match_tier"] = "REJECTED"
    else:
        result["match_score"] = raw
        result["match_tier"] = _tier_for(raw)

    # Normalização defensiva de strengths e gaps
    strengths = result.get("strengths")
    if isinstance(strengths, list):
        result["strengths"] = [str(s).strip() for s in strengths if str(s).strip()]
    elif isinstance(strengths, str) and strengths.strip():
        result["strengths"] = [strengths.strip()]
    else:
        result["strengths"] = []

    gaps = result.get("gaps")
    if isinstance(gaps, list):
        result["gaps"] = [str(g).strip() for g in gaps if str(g).strip()]
    elif isinstance(gaps, str) and gaps.strip():
        result["gaps"] = [gaps.strip()]
    else:
        result["gaps"] = []

    return result
