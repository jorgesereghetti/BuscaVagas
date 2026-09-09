"""Fonte única de verdade do matching currículo↔vaga.

Centraliza o perfil do candidato, a rubrica de avaliação e o schema de saída,
usados pelos providers Gemini e Groq.

CONTRATO v3: o LLM devolve 4 sub-scores (domain/skills/seniority/location) +
is_valid_grade + rationale. A combinação ponderada e o match_tier são
calculados em Python, NÃO aqui.
"""

import os

# Nunca embuta dados pessoais no código. O currículo real fica somente em
# backend/resume.txt, que é ignorado pelo Git.
_RESUME_FALLBACK = (
    "Currículo não configurado. Não presuma competências, formação, experiência "
    "ou localização; solicite que o usuário cadastre o currículo na interface."
)


def _load_resume() -> str:
    """Carrega o currículo COMPLETO de resume.txt (relativo a este arquivo).

    Fallback para o resumo condensado embutido se o arquivo não existir ou
    estiver vazio.
    """
    resume_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resume.txt")
    try:
        with open(resume_path, encoding="utf-8") as f:
            content = f.read().strip()
        return content if content else _RESUME_FALLBACK
    except OSError:
        return _RESUME_FALLBACK


# Currículo completo carregado em tempo de import.
RESUME = _load_resume()

# Perfil da VAGA IDEAL — usado no ranking por embeddings.
TARGET_PROFILE = """
Vaga ideal: Analista ou Consultor Júnior/Pleno de No-code, Low-code, Inteligência Artificial e Automação de Processos.
Responsabilidades: construir e manter agentes de IA, chatbots, assistentes virtuais;
trabalhar com LLM, RAG e prompt engineering; automação de fluxos com n8n, Make, Zapier, Bubble, Airtable ou Python de automação;
integração de APIs REST, webhooks e orquestração de soluções de IA aplicadas a negócios.
Modelo: Remoto ou São Paulo (presencial/híbrido).
NÃO é vaga de desenvolvimento de software tradicional (Java, C#, React puro), nem RPA legado corporativo, nem gerência/sênior.
""".strip()

REQUIRED_FIELDS = {
    "domain_score",
    "skills_score",
    "seniority_score",
    "location_score",
    "is_valid_grade",
    "rationale",
}

# Schema de saída estruturada (Gemini responseSchema / validação Groq).
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "domain_score": {"type": "integer"},
        "skills_score": {"type": "integer"},
        "seniority_score": {"type": "integer"},
        "location_score": {"type": "integer"},
        "is_valid_grade": {"type": "boolean"},
        "rationale": {"type": "string"},
        "strengths": {
            "type": "array",
            "items": {"type": "string"}
        },
        "gaps": {
            "type": "array",
            "items": {"type": "string"}
        },
    },
    "required": [
        "domain_score",
        "skills_score",
        "seniority_score",
        "location_score",
        "is_valid_grade",
        "rationale",
        "strengths",
        "gaps",
    ],
}


def build_match_prompt(title: str, company: str, desc: str) -> str:
    from backend.runtime import settings
    snapshot = settings()
    policy = snapshot["policy"]
    exclusions = ", ".join(snapshot["excluded_keywords"]) or "nenhum termo adicional"
    locations = ", ".join(policy["allowed_locations"]) or "nenhuma cidade presencial definida"
    return f"""Você é um recrutador técnico sênior brasileiro especialista em No-code, Low-code e IA para negócios.
Avalie objetivamente a compatibilidade entre o CURRÍCULO e a VAGA, com máxima precisão e rigor.

--- CURRÍCULO ---
{_load_resume()}

--- VAGA ---
Cargo: {title}
Empresa: {company}
Descrição: {desc}

--- SUA TAREFA ---
Atribua 4 SUB-NOTAS independentes (0–100 cada), avalie is_valid_grade, escreva rationale, e liste strengths e gaps.
NÃO calcule nem retorne match_score nem match_tier — esses são calculados em Python.

FILOSOFIA OBRIGATÓRIA:
O objetivo é ser ALTAMENTE RESTRITIVO E PRECISO. Focar exclusivamente em No-code / Low-code, Agentes de IA, Chatbots, IA Generativa aplicada a negócios e Automação de fluxos/APIs.

PILAR IDEAL (domain_score 75–100 e is_valid_grade = true):
- No-code / Low-code: n8n, Make, Zapier, Bubble, Airtable, Retool, automação de fluxos e rotinas de negócios
- Agentes de IA, Chatbots conversacionais (Zaia, WhatsApp API), Prompt Engineering, RAG prático e orquestração de LLMs
- Analista / Consultor de Automação de Processos com IA e Integração de APIs REST/Webhooks

REJEITAR DETERMINANTEMENTE — is_valid_grade = false (vaga é descartada/ocultada com match_score baixo):
- Vagas de Programação Tradicional / Engenharia de Software: Desenvolvedor (Backend, Frontend, Fullstack, Mobile), Engenheiro de Software, Desenvolvedor Python tradicional (Django/FastAPI pesado, microsserviços, OOP avançada, Java, C#, PHP, React, Angular, etc.).
- Vagas de Engenharia de IA / Machine Learning / Ciência de Dados Avançada: ML Engineer, Cientista de Dados, Engenheiro de Dados (pedindo treinamento de redes neurais do zero, PyTorch, TensorFlow, Spark, Databricks, matemática estatística avançada).
- Títulos contendo os termos proibidos configurados pelo candidato: {exclusions}.
- Vagas de RPA tradicional/corporativo legado (UiPath, Automation Anywhere, Blue Prism).
- Localização incompatível: presencial/híbrido fora das localidades configuradas ({locations}). Local desconhecido não deve ser inventado.
- Fora de tecnologia: gastronomia, operacional, comercial externo, vendas puras, atendimento call center.
- Docência / Ensino: professor, instrutor, tutor.
- Programas não-efetivos: estágio, aprendiz, jovem aprendiz, trainee.
- Suporte técnico, helpdesk, infraestrutura de TI, redes, sysadmin, DevOps, SRE, Cloud/QA Engineer.
- Análise de Dados / BI puro tradicional (apenas dashboards Power BI/SQL sem automação ou IA).

--- CRITÉRIOS DE SUB-NOTAS ---
domain_score (Proximidade com No-code, Low-code, Agentes de IA e Automação):
- 75–100: core da vaga é No-code, n8n, Make, Agentes de IA, Chatbots, LLMs para negócios ou automação de processos
- 50–74:  automação de rotinas com APIs onde low-code/IA é aplicável
- 1–49:   exige desenvolvimento tradicional de software ou ciência de dados (marcar is_valid_grade=false)
- 0:       totalmente fora de escopo (marcar is_valid_grade=false)

skills_score (Sobreposição de ferramentas práticas):
- 75–100: cita n8n, Make, Zapier, APIs REST, Webhooks, LLMs, LangChain, Supabase, Prompt Engineering, Claude, Python básico de automação
- 1–74:   cita ferramentas genéricas de produtividade
- 0:      exige stack pesada de programação ou bibliotecas que o candidato não domina (ex: React, Django, PyTorch)

seniority_score (Adequação Júnior / Pleno):
- 100 = Júnior ou Pleno acessível (foco em execução e ferramentas)
- 0 = Exige experiência muito superior à do currículo. Use os termos proibidos configurados para determinar exclusão por título.

location_score (Modelo de Trabalho):
- 100 = Remoto OU local compatível com: {locations}.
- 50 = Localização não informada: incluir um ponto de atenção, sem presumir remoto.
- 0 = Localização explicitamente incompatível com as preferências.

--- REGRAS DE RATIONALE, STRENGTHS E GAPS ---
- rationale: 2 a 3 frases concisas em PT-BR explicando o motivo da nota e compatibilidade das ferramentas.
- strengths: lista (array de strings) com 2 a 4 pontos fortes específicos que o candidato domina para essa vaga (ex: "Domínio de n8n e automação via APIs REST", "Experiência prática com agentes de IA e WhatsApp").
- gaps: lista (array de strings) com 1 a 3 pontos de atenção, tecnologias diferenciais pedidas na vaga que o candidato precisa reforçar/mencionar, ou motivo de descarte caso inválido (ex: "Pede conhecimento em Retool", "Desejável vivência com Bubble"). Se não houver gaps relevantes, retorne array vazio [].

A política configurada prevalece sobre exemplos gerais:
- Excluir programação tradicional: {policy['exclude_traditional_dev']}.
- Aceitar localização não informada para triagem: {policy['allow_unknown_location']}.
- Termos proibidos de título são exclusivamente os configurados: {exclusions}.
- Nunca atribua competências ou localização sem evidência no anúncio.

Retorne APENAS o JSON com os campos: domain_score, skills_score, seniority_score, location_score, is_valid_grade, rationale, strengths, gaps. NÃO inclua match_score, match_tier nem pitch."""
