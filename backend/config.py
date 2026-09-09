import os
import re
import json

# ── .env loader (sem dependência externa) ────────────────────────────────────
def _load_dotenv() -> None:
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_dotenv()

# ── Provider de LLM ───────────────────────────────────────────────────────────
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()

# ── Gemini ────────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_TIMEOUT = int(os.environ.get("GEMINI_TIMEOUT", "25"))

# Pré-ranqueamento semântico (embeddings)
USE_EMBEDDING_RANK = os.environ.get("USE_EMBEDDING_RANK", "true").lower() == "true"
GEMINI_EMBED_MODEL = os.environ.get("GEMINI_EMBED_MODEL", "gemini-embedding-001")

# ── Groq (fallback automático do Gemini) ──────────────────────────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_TIMEOUT = int(os.environ.get("GROQ_TIMEOUT", "25"))

# ── Ollama (local) ───────────────────────────────────────────────────────────
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.6:latest")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "120"))

# ── Janela Máxima de Idade de Vagas (em dias) ─────────────────────────────────
MAX_JOB_AGE_DAYS = int(os.environ.get("MAX_JOB_AGE_DAYS", "7"))

# ── Filtros de Localização e Limites ──────────────────────────────────────────
LOCATION_PREFS = ["são paulo", "sao paulo", "remoto", "remote"]
SCAN_LIMIT_DEFAULT = int(os.environ.get("SCAN_LIMIT", "50"))
GUPY_MAX_PER_QUERY = int(os.environ.get("GUPY_MAX_PER_QUERY", "40"))

# ── Fontes Adicionais ────────────────────────────────────────────────────────
USE_LINKEDIN = os.environ.get("USE_LINKEDIN", "true").lower() == "true"
LINKEDIN_LOCATION = os.environ.get("LINKEDIN_LOCATION", "Brasil")
LINKEDIN_MAX_PER_QUERY = int(os.environ.get("LINKEDIN_MAX_PER_QUERY", "25"))

USE_REMOTAR = os.environ.get("USE_REMOTAR", "true").lower() == "true"
REMOTAR_MAX_PER_QUERY = int(os.environ.get("REMOTAR_MAX_PER_QUERY", "20"))

USE_PROGRAMATHOR = os.environ.get("USE_PROGRAMATHOR", "true").lower() == "true"
PROGRAMATHOR_MAX_PER_QUERY = int(os.environ.get("PROGRAMATHOR_MAX_PER_QUERY", "20"))

# Caminho do JSON de configuração persistido
CONFIG_JSON_PATH = os.path.join(os.path.dirname(__file__), "config.json")

DEFAULT_EXCLUDED_KEYWORDS = [
    "sênior", "senior", "sr", "gerente", "manager", "diretor", "head", "especialista",
    "coordenador", "líder", "lead", "principal", "staff", "rpa", "uipath", "blue prism",
    "automation anywhere", "helpdesk", "suporte técnico", "infraestrutura", "redes",
    "sysadmin", "professor", "docente", "instrutor", "estágio", "estagiário", "aprendiz", "trainee",
    "engenheiro de software", "software engineer", "engenheiro de dados", "data engineer",
    "cientista de dados", "data scientist", "machine learning engineer", "ml engineer",
    "engenheiro de ml", "desenvolvedor backend", "backend developer", "desenvolvedor frontend",
    "frontend developer", "desenvolvedor fullstack", "fullstack developer", "full stack developer",
    "devops", "sre", "cloud engineer", "qa engineer", "tech lead", "arquiteto de software"
]


def build_exclude_regex(keywords: list[str]) -> re.Pattern:
    """Compila lista de palavras-chave em uma regex de fronteira de palavra."""
    escaped = [re.escape(k.strip()) for k in keywords if k.strip()]
    if not escaped:
        return re.compile(r"(?!)")
    pattern = r"\b(" + "|".join(escaped) + r")\b"
    return re.compile(pattern, re.IGNORECASE)


def normalize_config_list(value) -> list[str]:
    """Valida e remove termos vazios ou repetidos, preservando a ordem."""
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("Todas as listas devem ser arrays de strings.")
    return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def load_config():
    gupy_defaults = [
        "nocode", "lowcode", "n8n", "agentes de ia", "chatbot",
        "ia generativa automação", "make automação", "zapier", "automação de processos", "consultor ia"
    ]
    linkedin_defaults = [
        "nocode", "lowcode", "n8n", "agente de IA", "chatbot",
        "automação de processos", "prompt engineer", "ia generativa automação", "make automação", "consultor ia"
    ]
    remotar_defaults = ["n8n", "automação", "nocode", "lowcode", "chatbot", "agentes de ia"]
    programathor_defaults = ["n8n", "automação", "nocode", "lowcode", "chatbot"]
    excluded_defaults = DEFAULT_EXCLUDED_KEYWORDS

    if not os.path.exists(CONFIG_JSON_PATH):
        return gupy_defaults, linkedin_defaults, remotar_defaults, programathor_defaults, excluded_defaults

    try:
        with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            gupy = data.get("gupy_queries", gupy_defaults)
            linkedin = data.get("linkedin_queries", linkedin_defaults)
            remotar = data.get("remotar_queries", remotar_defaults)
            programathor = data.get("programathor_queries", programathor_defaults)
            excluded = data.get("excluded_keywords", excluded_defaults)
            return tuple(normalize_config_list(value) for value in
                         (gupy, linkedin, remotar, programathor, excluded))
    except Exception as e:
        print(f"[Config] Erro ao ler config.json, usando padrões: {e}")
        return gupy_defaults, linkedin_defaults, remotar_defaults, programathor_defaults, excluded_defaults


def reload_runtime_config():
    global GUPY_QUERIES, LINKEDIN_QUERIES, REMOTAR_QUERIES, PROGRAMATHOR_QUERIES, EXCLUDED_KEYWORDS, EXCLUDE_TITLE_RE
    GUPY_QUERIES, LINKEDIN_QUERIES, REMOTAR_QUERIES, PROGRAMATHOR_QUERIES, EXCLUDED_KEYWORDS = load_config()
    EXCLUDE_TITLE_RE = build_exclude_regex(EXCLUDED_KEYWORDS)
    return {
        "gupy_queries": GUPY_QUERIES,
        "linkedin_queries": LINKEDIN_QUERIES,
        "remotar_queries": REMOTAR_QUERIES,
        "programathor_queries": PROGRAMATHOR_QUERIES,
        "excluded_keywords": EXCLUDED_KEYWORDS,
    }


GUPY_QUERIES, LINKEDIN_QUERIES, REMOTAR_QUERIES, PROGRAMATHOR_QUERIES, EXCLUDED_KEYWORDS = load_config()
EXCLUDE_TITLE_RE = build_exclude_regex(EXCLUDED_KEYWORDS)


# ── Descarte de Desenvolvedor de Programação Tradicional ────────────────────────
_TRADITIONAL_DEV_KEYWORDS = (
    r"java|c#|\.net|dotnet|c\+\+|golang|rust|php|ruby|rails|kotlin|swift|flutter|"
    r"react|angular|vue|node(\.js)?|front[\s\-]?end|back[\s\-]?end|full[\s\-]?stack|"
    r"mobile|ios|android|engenheiro\s+de\s+software|software\s+engineer|programador(a)?|"
    r"engenheiro\s+de\s+dados|data\s+engineer|cientista\s+de\s+dados|data\s+scientist|"
    r"machine\s+learning\s+engineer|ml\s+engineer|engenheiro\s+de\s+ml|devops|sre|"
    r"cloud\s+engineer|qa\s+engineer|tech\s+lead|arquiteto\s+de\s+software"
)

_TRADITIONAL_DEV_RE = re.compile(
    rf"\b({_TRADITIONAL_DEV_KEYWORDS})\b",
    re.IGNORECASE
)

_GENERIC_DEV_RE = re.compile(
    r"\b(desenvolvedor(a)?|developer|programador(a)?|dev|engineer|engenheiro(a)?)\b",
    re.IGNORECASE
)

_AI_LOWCODE_KEYWORDS_RE = re.compile(
    r"\b(ia|i\.a\.|intelig[êe]ncia\s+artificial|agente[s]?(\s+de\s+ia)?|automa[cç][ãa]o|"
    r"nocode|no-code|lowcode|low-code|n8n|make|zapier|bubble|airtable|retool|"
    r"chatbot|llm|rag|prompt|ia\s+generativa|generative\s+ai)\b",
    re.IGNORECASE
)


def is_pure_dev_title(title: str) -> bool:
    """Retorna True se o título é de desenvolvimento tradicional com programação ou ciência de dados,
    sem menção estrita a No-code, Low-code, Agentes de IA ou Automação moderna."""
    title_lower = title.lower()

    if _TRADITIONAL_DEV_RE.search(title_lower):
        if not _AI_LOWCODE_KEYWORDS_RE.search(title_lower):
            return True

    if _GENERIC_DEV_RE.search(title_lower) and not _AI_LOWCODE_KEYWORDS_RE.search(title_lower):
        return True

    return False

# Política compartilhada pelos coletores, IA, limpeza e interface.
POLICY_DEFAULTS = {
    "max_job_age_days": MAX_JOB_AGE_DAYS,
    "filter_gupy_by_date": False,
    "allow_unknown_location": True,
    "min_match_score": 50,
    "exclude_traditional_dev": True,
    "allowed_locations": ["são paulo", "sao paulo"],
}
CONFIG_FIELDS = {
    "gupy_queries": "GUPY_QUERIES", "linkedin_queries": "LINKEDIN_QUERIES",
    "remotar_queries": "REMOTAR_QUERIES", "programathor_queries": "PROGRAMATHOR_QUERIES",
    "excluded_keywords": "EXCLUDED_KEYWORDS",
}


def validate_policy(value):
    if not isinstance(value, dict):
        raise ValueError("A política deve ser um objeto JSON.")
    unknown = set(value) - set(POLICY_DEFAULTS)
    if unknown:
        raise ValueError("Campo de política desconhecido: " + ", ".join(sorted(unknown)))
    result = {**POLICY_DEFAULTS, **value}
    for key, maximum in (("max_job_age_days", 3650), ("min_match_score", 100)):
        if type(result[key]) is not int or not 0 <= result[key] <= maximum:
            raise ValueError(f"{key} deve ser inteiro entre 0 e {maximum}.")
    for key in ("filter_gupy_by_date", "allow_unknown_location", "exclude_traditional_dev"):
        if type(result[key]) is not bool:
            raise ValueError(f"{key} deve ser booleano.")
    result["allowed_locations"] = normalize_config_list(result["allowed_locations"])
    return result


def runtime_snapshot():
    """Lê a configuração persistida para manter processos WSGI coerentes."""
    data = {}
    if os.path.exists(CONFIG_JSON_PATH):
        with open(CONFIG_JSON_PATH, encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError("config.json deve conter um objeto.")
    # Os módulos são recarregados depois de cada gravação da API; usar o estado
    # em memória evita que uma execução em andamento veja um arquivo parcialmente
    # trocado e também mantém a função determinística em testes.
    result = {key: normalize_config_list(globals()[attribute])
              for key, attribute in CONFIG_FIELDS.items()}
    result["policy"] = validate_policy(data.get("policy", {}))
    return result
