/** Normalização e enriquecimento de vagas sem dependência do DOM. */
(function exposeJobModel(global) {
  const KANBAN_COLUMNS = Object.freeze(["nova", "enviada", "oculta"]);
  const TECH_KEYWORDS = [
    "n8n", "LangChain", "OpenAI", "RAG", "LLM", "Python", "Make", "Zapier", "Bubble",
    "Next.js", "Supabase", "Cursor", "Claude", "Power Automate", "Copilot", "Airtable",
    "Retool", "Xano", "SQL", "Postgres", "Power BI", "FastAPI", "AWS", "Docker",
    "TypeScript", "JavaScript", "React", "APIs REST", "NLP", "Machine Learning", "IA Generativa"
  ];

  function normalizeStatus(status) {
    const value = String(status || "nova").toLowerCase();
    if (["applied", "enviada", "interviewing", "entrevista"].includes(value)) return "enviada";
    if (["ignored", "oculta", "rejected", "nao_compativel", "incompativel"].includes(value)) return "oculta";
    return "nova";
  }

  function detectPillar(title = "", description = "") {
    const text = `${title} ${description}`.toLowerCase();
    if (/n8n|make|zapier|bubble|airtable|retool|xano|nocode|no-code|lowcode|low-code/i.test(text)) return "nocode";
    if (/cursor|vibecoding|prompt\s*engineer|claude|v0|builder/i.test(text)) return "vibecoding";
    if (/agente|chatbot|llm|rag|ia\s*generativa|intelig[êe]ncia|automa[cç][ãa]o/i.test(text)) return "automacao";
    return "outros";
  }

  function detectWorkplace(place = "", title = "", description = "") {
    const text = `${place} ${title} ${description}`.toLowerCase();
    if (/remoto|remote|100%\s*remoto|home\s*office/i.test(text)) return "remoto";
    if (/h[íi]brido|hybrid/i.test(text)) return "hibrido";
    if (/presencial|on-site|onsite/i.test(text)) return "presencial";
    return "nao_informado";
  }

  function extractStack(title = "", description = "") {
    const text = `${title} ${description}`;
    const found = TECH_KEYWORDS.filter(keyword => {
      const escaped = keyword.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      return new RegExp(`\\b${escaped}\\b`, "i").test(text);
    });
    return found.slice(0, 4);
  }

  function extractLevel(title = "", description = "") {
    const text = `${title} ${description}`.toLowerCase();
    if (/s[êe]nior|sr\b/i.test(text)) return "Sênior";
    if (/especialista|specialist/i.test(text)) return "Especialista";
    if (/pleno|pl\b/i.test(text)) return "Pleno";
    if (/j[úu]nior|jr\b/i.test(text)) return "Júnior";
    if (/est[áa]gio|intern/i.test(text)) return "Estágio";
    return "Não informado";
  }

  function formatDate(dateStr) {
    if (!dateStr) return "data não informada";
    try {
      const date = new Date(String(dateStr).replace(" ", "T"));
      if (Number.isNaN(date.getTime())) return "data não informada";
      const diffHours = Math.max(0, Math.round((Date.now() - date.getTime()) / 3600000));
      if (diffHours < 24) return "hoje";
      const diffDays = Math.round(diffHours / 24);
      if (diffDays === 1) return "há 1 dia";
      if (diffDays <= 7) return `há ${diffDays} dias`;
      return `${String(date.getDate()).padStart(2, "0")}/${String(date.getMonth() + 1).padStart(2, "0")}`;
    } catch {
      return "data não informada";
    }
  }

  function parseReasons(job) {
    const reasons = [];
    let gap = "";
    if (typeof job.rationale === "string") {
      for (const sentence of job.rationale.split(/(?<=[.!?])\s+/)) {
        if (!sentence.trim()) continue;
        if (/lacuna|contudo|no entanto|porém|mas|exige|falta|ausência|não é o foco|rejeitada/i.test(sentence)) {
          if (!gap) gap = sentence.trim();
        } else if (reasons.length < 3) {
          reasons.push(sentence.trim());
        }
      }
    }
    if (!reasons.length) reasons.push(`Aderência registrada: ${job.match_score ?? 0}%. Justificativa não disponível.`);
    return { reasons, gap };
  }

  function safeUrl(value) {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
    } catch {
      return "#";
    }
  }

  function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function scoreColor(score) {
    if (score >= 85) return "var(--accent)";
    if (score >= 70) return "var(--acid)";
    if (score >= 50) return "var(--ink-2)";
    return "var(--muted)";
  }

  function toViewJob(job) {
    const score = Number.isFinite(job.match_score) ? job.match_score : 0;
    const { reasons, gap } = parseReasons(job);
    const description = job.description || "Sem descrição disponível.";
    return {
      id: String(job.id),
      title: job.title || "Vaga sem título",
      company: job.company || "Empresa não informada",
      place: job.location || "Local não informado",
      salary: job.salary || "Salário não informado",
      source: job.source || "Fonte não informada",
      posted: formatDate(job.posted_date),
      level: extractLevel(job.title, job.description),
      score,
      domainScore: job.domain_score ?? 0,
      skillsScore: job.skills_score ?? 0,
      seniorityScore: job.seniority_score ?? 0,
      locationScore: job.location_score ?? 0,
      pillar: detectPillar(job.title, job.description),
      workplace: ["remoto", "hibrido", "presencial", "nao_informado"].includes(job.workplace)
        ? job.workplace
        : detectWorkplace(job.location, job.title, job.description),
      stack: extractStack(job.title, job.description),
      status: normalizeStatus(job.status),
      link: safeUrl(job.url),
      description,
      reasons,
      gap,
      strengths: Array.isArray(job.strengths) ? job.strengths : [],
      gaps: Array.isArray(job.gaps) && job.gaps.length ? job.gaps : (gap ? [gap] : []),
    };
  }

  global.JobModel = Object.freeze({
    KANBAN_COLUMNS, normalizeStatus, detectPillar, detectWorkplace, extractStack,
    extractLevel, formatDate, parseReasons, safeUrl, escapeHtml, scoreColor, toViewJob,
  });
})(globalThis);
