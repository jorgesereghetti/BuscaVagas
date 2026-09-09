"""Regressões locais: banco temporário e serviços externos simulados."""
import csv
import io
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from backend import config, db, engine, scoring
from backend.sources import gupy, linkedin
from backend.parsing import normalize_url, strip_html
import server


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(db, "DB_PATH", str(Path(self.temp.name) / "jobs.db")))
        db.init_db()

    def save(self, job_id="1", **kwargs):
        job = {"id": job_id, "url": f"https://example.com/jobs/{job_id}",
               "title": "Analista n8n", "company": "Empresa", "description": "Automação"}
        db.save_match(job, 80, "HIGH", kwargs.pop("rationale", "Compatível"), **kwargs)
        return job

    def test_distinct_url_prefix_is_not_duplicate(self):
        self.save("1234")
        self.assertIsNone(db.get_job_evaluation("different", url="https://example.com/jobs/123"))

    def test_legacy_tracking_url_still_matches(self):
        self.save("123")
        with db._conn() as conn:
            conn.execute("UPDATE jobs SET url = url || '/?utm_source=test'")
        result = db.get_job_evaluation("different", url="https://example.com/jobs/123?source=other")
        self.assertEqual(result["id"], "123")

    def test_reprocessing_preserves_application_and_date(self):
        job = self.save(status="enviada")
        with db._conn() as conn:
            conn.execute("UPDATE jobs SET date_added = '2020-01-01 00:00:00'")
        db.save_match({**job, "id": "another"}, 90, "HIGH", "Nova análise")
        saved = db.get_jobs()[0]
        self.assertEqual((saved["id"], saved["status"], saved["date_added"]),
                         ("1", "enviada", "2020-01-01 00:00:00"))

    def test_failed_legacy_evaluation_can_be_retried(self):
        job = self.save(status="oculta", rationale="LLM falhou em processar esta vaga.")
        db.save_match(job, 85, "HIGH", "Reavaliada")
        saved = db.get_jobs()[0]
        self.assertEqual((saved["status"], saved["match_score"]), ("nova", 85))

    def test_discard_missing_and_existing(self):
        self.assertFalse(db.discard_job("missing"))
        self.save()
        self.assertTrue(db.discard_job("1"))
        self.assertEqual(db.get_jobs()[0]["status"], "oculta")

    def test_legacy_statuses_are_canonical_and_purge_preserves_sent_jobs(self):
        for index, status in enumerate(("nova", "oculta", "analisada", "enviada", "selecionada")):
            self.save(str(index), status=status)
        self.assertEqual(db.purge_jobs(), 4)
        self.assertEqual({job["status"] for job in db.get_jobs()}, {"enviada"})

    def test_status_api_maps_legacy_flow_to_three_visible_states(self):
        self.save()
        for legacy, expected in (("analisada", "nova"), ("selecionada", "nova"),
                                 ("entrevista", "enviada"), ("ignored", "oculta")):
            with self.subTest(legacy=legacy):
                self.assertTrue(db.update_job_status("1", legacy))
                self.assertEqual(db.get_jobs()[0]["status"], expected)

    def test_database_migration_collapses_old_statuses(self):
        self.save("legacy")
        with db.connection(write=True) as conn:
            conn.execute("UPDATE jobs SET status='entrevista' WHERE id='legacy'")
            conn.execute("PRAGMA user_version=1")
        db.init_db()
        self.assertEqual(db.get_jobs()[0]["status"], "enviada")
        with db.connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_api_invalid_payloads_are_client_errors(self):
        client = server.app.test_client()
        for endpoint in ("/api/config", "/api/resume", "/api/jobs/1/status"):
            for payload in ([], [1], "text", 1):
                with self.subTest(endpoint=endpoint, payload=payload):
                    self.assertEqual(client.post(endpoint, json=payload).status_code, 400)
        for endpoint, payload in (("/api/resume", {"resume": 42}),
                                  ("/api/jobs/1/status", {"status": []}),
                                  ("/api/config", {"gupy_queries": [42]})):
            self.assertEqual(client.post(endpoint, json=payload).status_code, 400)

    def test_api_status_and_delete_missing(self):
        client = server.app.test_client()
        self.assertEqual(client.delete("/api/jobs/missing").status_code, 404)
        self.save()
        self.assertEqual(client.post("/api/jobs/1/status", json={"status": "applied"}).status_code, 200)
        self.assertEqual(client.get("/api/jobs?status=enviada").json[0]["status"], "enviada")

    def test_csv_external_text_is_not_a_formula(self):
        job = {"id": "csv", "url": "https://example.com/job", "title": '=HYPERLINK("bad")'}
        db.save_match(job, 80, "HIGH", "Boa", status="enviada")
        response = server.app.test_client().get("/api/jobs/export")
        rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
        self.assertTrue(rows[1][0].startswith("'="))

    def test_engine_reads_latest_config_and_ranks_only_unseen(self):
        known = self.save("known")
        fresh = {**known, "id": "fresh", "url": "https://example.com/jobs/fresh", "title": "Consultor n8n", "location": "São Paulo (Híbrido)"}
        with ExitStack() as stack:
            for name in ("USE_LINKEDIN", "USE_REMOTAR", "USE_PROGRAMATHOR"):
                stack.enter_context(patch.object(engine, name, False))
            stack.enter_context(patch.object(config, "GUPY_QUERIES", ["termo atualizado"]))
            stack.enter_context(patch.object(config, "EXCLUDE_TITLE_RE", config.build_exclude_regex([])))
            stack.enter_context(patch.object(engine, "USE_EMBEDDING_RANK", True))
            stack.enter_context(patch.object(engine.time, "sleep"))
            fetch = stack.enter_context(patch.object(engine, "fetch_gupy_jobs", return_value=[known, fresh]))
            rank = stack.enter_context(patch.object(engine, "rank_by_similarity", side_effect=lambda jobs: jobs))
            score = stack.enter_context(patch.object(engine, "score_job", return_value=None))
            self.assertEqual(engine.process_and_match_jobs(), 0)
        self.assertEqual(fetch.call_args.args, ("termo atualizado",))
        self.assertEqual([job["id"] for job in rank.call_args.args[0]], ["fresh"])
        self.assertIn("São Paulo (Híbrido)", score.call_args.args[2])
        self.assertIsNone(db.get_job_evaluation("fresh"))


class ConfigAndScoringTests(unittest.TestCase):
    def test_shared_parsing_preserves_lines_and_normalizes_tracking(self):
        self.assertEqual(strip_html('<p>IA&nbsp;&amp; automação</p><p>n8n<br>APIs</p>'),
                         'IA & automação\nn8n\nAPIs')
        self.assertEqual(normalize_url('https://EXAMPLE.com/jobs/123/?utm_source=test#apply'),
                         'https://example.com/jobs/123')

    def test_interrupted_atomic_write_preserves_original_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'resume.txt'
            path.write_text('Currículo original')
            with patch.object(server.os, 'replace', side_effect=OSError('falha simulada')):
                with self.assertRaises(OSError):
                    server._write_text_atomic(str(path), 'Novo currículo')
            self.assertEqual(path.read_text(), 'Currículo original')
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_blank_exclusions_never_match(self):
        self.assertIsNone(config.build_exclude_regex([" ", ""]).search("Analista n8n"))

    def test_config_partial_update_preserves_other_lists(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            path = str(Path(directory) / "config.json")
            stack.enter_context(patch.object(server, "CONFIG_PATH", path))
            stack.enter_context(patch.object(config, "CONFIG_JSON_PATH", path))
            for key in ("GUPY_QUERIES", "LINKEDIN_QUERIES", "REMOTAR_QUERIES", "PROGRAMATHOR_QUERIES", "EXCLUDED_KEYWORDS", "EXCLUDE_TITLE_RE"):
                stack.enter_context(patch.object(config, key, getattr(config, key)))
            previous = list(config.LINKEDIN_QUERIES)
            response = server.app.test_client().post("/api/config", json={"gupy_queries": [" n8n ", "n8n", ""]})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(config.GUPY_QUERIES, ["n8n"])
            self.assertEqual(config.LINKEDIN_QUERIES, previous)
            self.assertEqual(json.loads(Path(path).read_text())["gupy_queries"], ["n8n"])

    def test_false_string_cannot_approve_a_job(self):
        result = scoring.finalize_score({**dict.fromkeys(scoring.WEIGHTS, 100), "is_valid_grade": "false"})
        self.assertIs(result["is_valid_grade"], False)
        self.assertEqual(result["match_tier"], "REJECTED")

    def test_malformed_subscore_does_not_crash(self):
        result = scoring.finalize_score({"domain_score": float("inf"), "rationale": [], "is_valid_grade": True})
        self.assertEqual(result["domain_score"], 0)
        self.assertEqual(result["rationale"], "")

    def test_linkedin_repeated_rejected_page_stops(self):
        html = '<li><a href="https://www.linkedin.com/jobs/view/123">Vaga</a><h3 class="base-search-card__title">Analista n8n</h3><span class="job-search-card__location">Paris</span></li>'
        with patch.object(linkedin.requests, "get", return_value=Mock(status_code=200, text=html)) as get, patch.object(linkedin.time, "sleep"):
            self.assertEqual(linkedin.fetch_linkedin_jobs("n8n"), [])
            self.assertEqual(get.call_count, 2)

    def test_gupy_repeated_rejected_page_stops(self):
        response = Mock(status_code=200)
        response.json.return_value = {"data": [{"jobUrl": "https://example.com/inactive/123"}], "pagination": {"total": 10000}}
        with patch.object(gupy.requests, "get", return_value=response) as get, patch.object(gupy.time, "sleep"):
            self.assertEqual(gupy.fetch_gupy_jobs("n8n"), [])
            self.assertEqual(get.call_count, 2)


if __name__ == "__main__":
    unittest.main()

class NewFeaturesTests(unittest.TestCase):
    def test_structured_job_posting_and_unknown_policy(self):
        from backend.sources.structured import extract_job, job_postings
        from backend.policy import accepts
        html = '''<script type="application/ld+json">{"@type":"JobPosting","title":"Analista IA","description":"<p>n8n\nAPIs</p>","datePosted":"2026-09-09","hiringOrganization":{"name":"Acme"},"jobLocationType":"TELECOMMUTE"}</script>'''
        posting = next(job_postings(html))
        job = {"source": "Programathor", **extract_job(posting)}
        self.assertEqual((job["company"], job["location"], job["description"]), ("Acme", "Remoto", "n8n\nAPIs"))
        snapshot = {"excluded_keywords": [], "policy": {"max_job_age_days": 7, "filter_gupy_by_date": False, "allow_unknown_location": False, "exclude_traditional_dev": True, "min_match_score": 50, "allowed_locations": ["são paulo"]}}
        self.assertFalse(accepts({"title": "Analista IA", "location": None, "source": "LinkedIn"}, snapshot))

    def test_source_get_reports_http_failure_and_cancel(self):
        from backend.runtime import source_get, SourceError, ScanControl, execution
        with patch("backend.runtime.requests.get", return_value=Mock(status_code=503)):
            with self.assertRaises(SourceError):
                source_get("https://example.invalid", timeout=1)
        control = ScanControl(); control.event.set()
        with execution(control):
            from backend.runtime import ScanCancelled
            with self.assertRaises(ScanCancelled):
                source_get("https://example.invalid", timeout=1)

    def test_scan_lease_claim_and_cancel(self):
        from backend import scans
        with tempfile.TemporaryDirectory() as directory, patch.object(db, "DB_PATH", str(Path(directory) / "scan.db")):
            db.init_db()
            first = scans.enqueue()
            self.assertIsNotNone(first)
            self.assertIsNone(scans.enqueue())
            self.assertEqual(scans.claim_next(), first)
            self.assertTrue(scans.request_cancel())
            self.assertTrue(scans.is_cancelled(first))

    def test_stale_scan_returns_to_persistent_queue(self):
        from backend import scans
        with tempfile.TemporaryDirectory() as directory, patch.object(db, "DB_PATH", str(Path(directory) / "scan.db")):
            db.init_db()
            run_id = scans.enqueue()
            self.assertEqual(scans.claim_next(), run_id)
            with db.connection(write=True) as conn:
                conn.execute("UPDATE scan_runs SET heartbeat=0 WHERE id=?", (run_id,))
            status = scans.latest()
            self.assertEqual(status["state"], "queued")
            self.assertTrue(status["is_running"])

class ApiPolicyTests(unittest.TestCase):
    def test_policy_is_saved_without_erasing_queries(self):
        from backend import config
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            config_path = str(Path(directory) / "config.json")
            stack.enter_context(patch.object(server, "CONFIG_PATH", config_path))
            stack.enter_context(patch.object(config, "CONFIG_JSON_PATH", config_path))
            response = server.app.test_client().post("/api/config", json={"policy": {"min_match_score": 65, "allow_unknown_location": False}})
            self.assertEqual(response.status_code, 200)
            saved = json.loads(Path(config_path).read_text())
            self.assertEqual(saved["min_match_score"] if "min_match_score" in saved else saved["policy"]["min_match_score"], 65)
            self.assertFalse(saved["policy"]["allow_unknown_location"])
            self.assertIn("gupy_queries", saved)

    def test_external_access_requires_password(self):
        app = server.create_app({"TESTING": True}, initialize=False)
        with app.test_client() as client:
            response = client.get("/api/config", environ_base={"REMOTE_ADDR": "10.0.0.2"})
            self.assertEqual(response.status_code, 403)
