from datetime import datetime

from flask import Blueprint, Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import BadRequest

import json
import os
import tempfile
import fcntl
from contextlib import contextmanager
from werkzeug.security import check_password_hash
from backend import scans
from backend.policy import STAGES
from backend.engine import process_and_match_jobs
from backend.db import init_db, get_jobs, get_stats, update_job_status, discard_job, ignore_all_new_jobs, purge_jobs, STATUS_ALIASES
import backend.config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

routes = Blueprint("radar", __name__)


@contextmanager
def config_lock():
    # Serializa gravações também entre processos do servidor.
    with open(CONFIG_PATH + ".lock", "a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _json_object():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadRequest("Envie um objeto JSON válido.")
    return data


@routes.errorhandler(BadRequest)
def invalid_request(error):
    return jsonify({"error": error.description}), 400


def _write_text_atomic(path, content):
    """Substitui o arquivo apenas depois que a escrita completa termina."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=os.path.dirname(path), delete=False) as stream:
            temporary = stream.name
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)

@routes.route("/")
def serve_index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@routes.route("/<path:path>")
def serve_assets(path):
    return send_from_directory(FRONTEND_DIR, path)


@routes.route("/api/jobs", methods=["GET"])
def api_get_jobs():
    include_rejected = request.args.get("include_rejected", "false").lower() == "true"
    return jsonify(get_jobs(
        status=request.args.get("status"),
        tier=request.args.get("tier"),
        include_rejected=include_rejected
    ))


@routes.route("/api/jobs/<job_id>", methods=["DELETE"])
def api_discard_job(job_id):
    if discard_job(job_id):
        return jsonify({"message": "Vaga movida para Não Compatível."})
    return jsonify({"error": "Vaga não encontrada."}), 404


@routes.route("/api/jobs/clear-new", methods=["POST"])
def api_clear_new_jobs():
    ignore_all_new_jobs()
    return jsonify({"message": "Todas as novas vagas foram ocultadas."})


@routes.route("/api/jobs/purge", methods=["POST"])
def api_purge_jobs():
    count = purge_jobs()
    return jsonify({"message": f"{count} vagas removidas permanentemente. Vagas enviadas foram preservadas."})


@routes.route("/api/jobs/<job_id>/status", methods=["POST"])
def api_update_status(job_id):
    new_status = _json_object().get("status")
    if not isinstance(new_status, str) or new_status not in STATUS_ALIASES:
        return jsonify({"error": "Status inválido."}), 400

    if update_job_status(job_id, new_status):
        return jsonify({"message": f"Status atualizado para '{new_status}'."})
    return jsonify({"error": "Vaga não encontrada."}), 404


@routes.route("/api/jobs/export", methods=["GET"])
def api_export_jobs():
    """Exporta candidaturas em CSV estruturado para acompanhamento."""
    import csv
    import io
    from flask import Response

    status_filter = request.args.get("status", "enviada")
    jobs = get_jobs(status=status_filter, include_rejected=True)

    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["Título", "Empresa", "Local", "Score", "Origem", "Link", "Data", "Justificativa IA"])

    for j in jobs:
        # Conteúdo de fontes externas deve ser texto, nunca fórmula de planilha.
        writer.writerow([_csv_text(value) for value in [
            j.get("title", ""),
            j.get("company", ""),
            j.get("location", ""),
            f"{j.get('match_score', 0)}%",
            j.get("source", ""),
            j.get("url", ""),
            j.get("posted_date", "") or j.get("date_added", ""),
            j.get("rationale", "")
        ]])

    csv_data = output.getvalue()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=candidaturas_radar_{datetime.now().strftime('%Y%m%d')}.csv"}
    )


RESUME_PATH = os.path.join(BASE_DIR, "backend", "resume.txt")
CONFIG_PATH = os.path.join(BASE_DIR, "backend", "config.json")


def _csv_text(value):
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


@routes.route("/api/resume", methods=["GET"])
def api_get_resume():
    try:
        with open(RESUME_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        return jsonify({"resume": content})
    except FileNotFoundError:
        return jsonify({"resume": ""})
    except Exception as e:
        return jsonify({"error": f"Erro ao ler currículo: {str(e)}"}), 500


@routes.route("/api/resume", methods=["POST"])
def api_save_resume():
    content = _json_object().get("resume")
    if not isinstance(content, str) or not content.strip():
        return jsonify({"error": "O conteúdo do currículo não pode ser vazio."}), 400
    content = content.strip()

    try:
        # Atualiza o currículo em memória no prompt
        import backend.prompt
        with config_lock():
            _write_text_atomic(RESUME_PATH, content)
            backend.prompt.RESUME = content
        return jsonify({"message": "Currículo atualizado com sucesso!"})
    except Exception as e:
        return jsonify({"error": f"Erro ao salvar currículo: {str(e)}"}), 500


@routes.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify({**backend.config.runtime_snapshot(), "stages": STAGES})


@routes.route("/api/config", methods=["POST"])
def api_save_config():
    data = _json_object()
    try:
        with config_lock():
            current = backend.config.runtime_snapshot()
            payload = {
                key: backend.config.normalize_config_list(data.get(key, current[key]))
                for key in backend.config.CONFIG_FIELDS
            }
            incoming_policy = data.get("policy", {})
            if not isinstance(incoming_policy, dict):
                raise ValueError("A política deve ser um objeto.")
            payload["policy"] = backend.config.validate_policy({**current["policy"], **incoming_policy})
            _write_text_atomic(CONFIG_PATH, json.dumps(payload, indent=2, ensure_ascii=False))
            backend.config.reload_runtime_config()
        return jsonify({"message": "Configurações de busca e exclusões atualizadas com sucesso!"})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except OSError as e:
        return jsonify({"error": f"Erro ao salvar configurações: {e}"}), 500




@routes.route("/api/scan", methods=["POST"])
def api_trigger_scan():
    run_id = scans.enqueue()
    if run_id is None:
        return jsonify({"message": "Busca já em execução.", "status": scans.latest()}), 409
    try:
        scans.kick(process_and_match_jobs)
    except Exception:
        scans.fail(run_id, "Não foi possível iniciar a busca.")
        return jsonify({"error": "Não foi possível iniciar a busca."}), 500
    return jsonify({"message": "Busca iniciada em segundo plano.", "status": scans.latest()}), 202


@routes.route("/api/scan/stop", methods=["POST"])
def api_stop_scan():
    if not scans.request_cancel():
        return jsonify({"message": "Nenhuma busca em execução."}), 409
    return jsonify({"message": "Cancelamento solicitado. A requisição atual pode levar alguns segundos para terminar."})


@routes.route("/api/scan/status", methods=["GET"])
def api_scan_status():
    return jsonify({**scans.latest(), "stats": get_stats()})


def create_app(test_config=None, initialize=True):
    application = Flask(__name__, static_folder=None)
    application.config.update(MAX_CONTENT_LENGTH=1024 * 1024,
        AUTH_USER=os.environ.get("APP_USER", "admin"), AUTH_PASSWORD_HASH=os.environ.get("APP_PASSWORD_HASH", ""))
    if test_config:
        application.config.update(test_config)
    if initialize:
        init_db()
        scans.kick(process_and_match_jobs)
    application.register_blueprint(routes)

    @application.before_request
    def access_control():
        password_hash = application.config["AUTH_PASSWORD_HASH"]
        if password_hash:
            credentials = request.authorization
            if (not credentials or credentials.type != "basic"
                    or credentials.username != application.config["AUTH_USER"]
                    or not check_password_hash(password_hash, credentials.password or "")):
                return jsonify({"error": "Autenticação necessária."}), 401, {"WWW-Authenticate": 'Basic realm="BuscaVagas", charset="UTF-8"'}
        elif request.remote_addr not in ("127.0.0.1", "::1", None):
            return jsonify({"error": "Configure autenticação antes de permitir acesso pela rede."}), 403
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("Origin")
            if request.headers.get("Sec-Fetch-Site") == "cross-site" or (origin and origin != request.host_url.rstrip("/")):
                return jsonify({"error": "Origem da requisição não permitida."}), 403

    @application.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    return application


# Compatibilidade com testes/imports sem modificar o banco durante importação.
app = create_app(initialize=False)

if __name__ == "__main__":
    application = create_app()
    host = os.environ.get("HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1") and not application.config["AUTH_PASSWORD_HASH"]:
        raise SystemExit("Configure APP_PASSWORD_HASH antes de abrir acesso pela rede.")
    application.run(host=host, port=int(os.environ.get("PORT", "5001")), debug=False)
