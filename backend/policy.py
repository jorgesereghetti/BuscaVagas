"""Regras de seleção aplicadas igualmente a todas as fontes."""
import re
from datetime import datetime, timezone
from backend import config

STAGES = [
    {"id": "nova", "label": "Novas"},
    {"id": "enviada", "label": "Enviadas"},
    {"id": "oculta", "label": "Não compatíveis"},
]
STATUS_ALIASES = {stage["id"]: stage["id"] for stage in STAGES}
# Estados antigos continuam aceitos na API, mas são sempre persistidos em uma
# das três colunas visíveis. Assim integrações antigas não criam vagas órfãs.
STATUS_ALIASES.update(
    new="nova", analisada="nova", analyzed="nova", selecionada="nova", selected="nova",
    applied="enviada", entrevista="enviada", interviewing="enviada",
    ignored="oculta", rejected="oculta", nao_compativel="oculta", incompativel="oculta",
)


def parse_date(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        date = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date
    except ValueError:
        return None


def workplace(location):
    text = (location or "").casefold()
    if re.search(r"\b(h[íi]brido|hybrid)\b", text):
        return "hibrido"
    if re.search(r"\b(remoto|remote|telecommute|home office)\b", text):
        return "remoto"
    if re.search(r"\b(presencial|on.site|onsite)\b", text):
        return "presencial"
    return "nao_informado"


def is_expired(job, policy, now=None):
    now = now or datetime.now(timezone.utc)
    deadline = parse_date(job.get("application_deadline"))
    if deadline and deadline < now:
        return True
    if job.get("source") == "Gupy" and not policy["filter_gupy_by_date"]:
        return False
    age = policy["max_job_age_days"]
    posted = parse_date(job.get("posted_date"))
    return bool(age is not None and posted and (now.date() - posted.date()).days > age)


def accepts(job, snapshot):
    policy = snapshot["policy"]
    title = job.get("title") or ""
    if config.build_exclude_regex(snapshot["excluded_keywords"]).search(title):
        return False
    if policy["exclude_traditional_dev"] and config.is_pure_dev_title(title):
        return False
    if is_expired(job, policy):
        return False
    location = (job.get("location") or "").casefold()
    mode = job.get("workplace") or workplace(location)
    if mode == "remoto":
        return True
    if any(term.casefold() in location for term in policy["allowed_locations"]):
        return True
    if not location or location in ("brasil", "brazil", "não informado", "não informada"):
        return policy["allow_unknown_location"]
    return False
