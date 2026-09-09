"""Coleta, seleção e avaliação com estado isolado por busca."""
import time
from backend import config
from backend.db import init_db, save_match, get_job_evaluations
from backend.llm import score_job
from backend.embeddings import rank_by_similarity
from backend.sources.gupy import fetch_gupy_jobs, deduplicate
from backend.sources.linkedin import fetch_linkedin_jobs
from backend.sources.remotar import fetch_remotar_jobs
from backend.sources.programathor import fetch_programathor_jobs
from backend.policy import accepts
from backend.runtime import ScanControl, ScanResult, SourceReport, ScanCancelled, execution, collection, checkpoint

# Aliases mantidos para integrações e testes antigos; a configuração persistida
# continua sendo a fonte de verdade da execução.
USE_LINKEDIN = config.USE_LINKEDIN
USE_REMOTAR = config.USE_REMOTAR
USE_PROGRAMATHOR = config.USE_PROGRAMATHOR
USE_EMBEDDING_RANK = config.USE_EMBEDDING_RANK


def process_and_match_jobs(limit=None, progress_callback=None, control=None, result_callback=None):
    """Retorna o total de matches; diagnóstico completo segue pelo callback."""
    init_db()
    control = control or ScanControl()
    snapshot = config.runtime_snapshot()
    limit = config.SCAN_LIMIT_DEFAULT if limit is None else max(0, limit)
    result = ScanResult()

    def report(text, current=0, total=0):
        if progress_callback:
            progress_callback(text, current, total)
        if result_callback:
            result_callback(result.as_dict())

    try:
        with execution(control, snapshot):
            checkpoint()
            raw = []
            sources = [
                ("Gupy", True, "gupy_queries", fetch_gupy_jobs, config.GUPY_MAX_PER_QUERY),
                ("LinkedIn", globals().get("USE_LINKEDIN", config.USE_LINKEDIN), "linkedin_queries", fetch_linkedin_jobs, config.LINKEDIN_MAX_PER_QUERY),
                ("Remotar", globals().get("USE_REMOTAR", config.USE_REMOTAR), "remotar_queries", fetch_remotar_jobs, config.REMOTAR_MAX_PER_QUERY),
                ("Programathor", globals().get("USE_PROGRAMATHOR", config.USE_PROGRAMATHOR), "programathor_queries", fetch_programathor_jobs, config.PROGRAMATHOR_MAX_PER_QUERY),
            ]
            for name, enabled, key, fetch, maximum in sources:
                if not enabled:
                    continue
                source_report = SourceReport()
                result.sources[name] = source_report
                queries = snapshot[key]
                for index, query in enumerate(queries, 1):
                    checkpoint()
                    report(f"{name} ({index}/{len(queries)}): {query}", index, len(queries))
                    source_report.queries += 1
                    with collection(source_report):
                        jobs = fetch(query, max_results=maximum)
                    checkpoint()
                    source_report.collected += len(jobs)
                    raw.extend(jobs)
                report(f"{name}: {source_report.collected} vagas, {len(source_report.errors)} alertas")
            candidates = deduplicate(raw)
            result.duplicates = len(raw) - len(candidates)
            selected = [job for job in candidates if accepts(job, snapshot)]
            result.filtered = len(candidates) - len(selected)
            checkpoint()
            evaluations = get_job_evaluations(selected)
            unseen = []
            for job in selected:
                existing = evaluations.get(job["id"])
                if existing is None:
                    unseen.append(job)
                elif ((existing.get("rationale") or "").startswith("LLM falhou")
                      and existing["status"] in ("oculta", "ignored")):
                    job["id"] = existing["id"]
                    unseen.append(job)
                else:
                    result.duplicates += 1
            if globals().get("USE_EMBEDDING_RANK", config.USE_EMBEDDING_RANK) and unseen:
                report(f"Ordenando {len(unseen)} vagas novas por afinidade", 0, len(unseen))
                unseen = rank_by_similarity(unseen)
            target = unseen[:limit]
            for index, job in enumerate(target, 1):
                checkpoint()
                report(f"Avaliando ({index}/{len(target)}): {job['title'][:60]}", index, len(target))
                description = f"Localização informada pela fonte: {job.get('location') or 'Não informada'}\n\n{job.get('description') or '(sem descrição disponível)'}"
                evaluation = score_job(job["title"], job.get("company") or "Não informada", description)
                checkpoint()
                if evaluation is None:
                    result.llm_failures += 1
                    continue
                result.evaluated += 1
                accepted = (evaluation["is_valid_grade"] and evaluation["match_tier"] != "REJECTED"
                            and evaluation["match_score"] >= snapshot["policy"]["min_match_score"])
                saved = save_match(job, evaluation["match_score"], evaluation["match_tier"], evaluation["rationale"],
                    status="nova" if accepted else "oculta",
                    subscores={key: evaluation.get(key) for key in ("domain_score", "skills_score", "seniority_score", "location_score")},
                    strengths=evaluation.get("strengths"), gaps=evaluation.get("gaps"))
                if accepted and saved:
                    result.matched += 1
                report(f"{result.evaluated} avaliadas; {result.llm_failures} falhas de IA", index, len(target))
            has_errors = result.llm_failures or any(source.errors for source in result.sources.values())
            if has_errors:
                result.state = "partial" if result.evaluated or raw else "failed"
            report(f"{result.matched} novos matches; {result.evaluated} avaliadas; {result.llm_failures} falhas de IA", len(target), len(target))
    except ScanCancelled:
        result.state = "cancelled"
        report(f"Busca cancelada; {result.matched} matches já salvos foram preservados")
    finally:
        if result_callback:
            result_callback(result.as_dict())
    return result.matched
