"""Fila persistida de buscas, compartilhada entre processos."""
import json
import threading
import time
import uuid
from backend.db import connection
from backend.runtime import ScanControl

# Heartbeat independente de chamadas HTTP lentas; execução órfã expira em 60 s.
LEASE_SECONDS = 60
HISTORY_LIMIT = 100
_worker = None
_worker_lock = threading.Lock()
_worker_wakeup = threading.Event()


def _expire(conn):
    now = time.time()
    conn.execute("""UPDATE scan_runs SET state='cancelled', finished_at=?,
        progress_text='Busca cancelada' WHERE state='running' AND cancel_requested=1
        AND heartbeat < ?""", (now, now - LEASE_SECONDS))
    conn.execute("""UPDATE scan_runs SET state='queued', heartbeat=?, finished_at=NULL,
        error='Execução anterior interrompida; busca retomada automaticamente.',
        progress_text='Aguardando retomada' WHERE state='running' AND heartbeat < ?""",
        (now, now - LEASE_SECONDS))


def enqueue():
    with connection(write=True) as conn:
        _expire(conn)
        if conn.execute("SELECT 1 FROM scan_runs WHERE state IN ('queued','running')").fetchone():
            return None
        run_id = uuid.uuid4().hex
        now = time.time()
        conn.execute("INSERT INTO scan_runs(id,state,started_at,heartbeat,progress_text) VALUES (?,'queued',?,?,?)",
                     (run_id, now, now, 'Busca adicionada à fila'))
    return run_id


def claim_next():
    """Reserva atomicamente a próxima busca para este processo."""
    with connection(write=True) as conn:
        _expire(conn)
        if conn.execute("SELECT 1 FROM scan_runs WHERE state='running'").fetchone():
            return None
        row = conn.execute("""SELECT id FROM scan_runs WHERE state='queued' AND cancel_requested=0
                            ORDER BY started_at LIMIT 1""").fetchone()
        if row is None:
            return None
        now = time.time()
        updated = conn.execute("""UPDATE scan_runs SET state='running', heartbeat=?,
                               progress_text='Iniciando coleta de vagas...' WHERE id=? AND state='queued'""",
                               (now, row["id"])).rowcount
        return row["id"] if updated else None


def latest():
    with connection(write=True) as conn:
        _expire(conn)
        row = conn.execute("SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    if row is None:
        return {"is_running": False, "state": "idle", "progress_text": "agente ocioso", "jobs_found_last_run": 0, "current": 0, "total": 0, "error": None, "result": {}}
    result = json.loads(row["result"])
    return {**dict(row), "is_running": row["state"] in ("queued", "running"), "result": result,
            "jobs_found_last_run": result.get("matched", 0)}


def request_cancel():
    with connection(write=True) as conn:
        now = time.time()
        queued = conn.execute("""UPDATE scan_runs SET cancel_requested=1, state='cancelled',
                              progress_text='Busca cancelada', finished_at=? WHERE state='queued'""", (now,)).rowcount
        running = conn.execute("""UPDATE scan_runs SET cancel_requested=1,
                               progress_text='Cancelando busca...' WHERE state='running'""").rowcount
        return queued + running > 0


def is_cancelled(run_id):
    with connection() as conn:
        row = conn.execute("SELECT cancel_requested,state FROM scan_runs WHERE id=?", (run_id,)).fetchone()
    return row is None or row["cancel_requested"] or row["state"] != "running"


def _heartbeat(run_id, finished):
    while not finished.wait(5):
        try:
            with connection(write=True) as conn:
                conn.execute("UPDATE scan_runs SET heartbeat=? WHERE id=? AND state='running'", (time.time(), run_id))
        except Exception:
            # A concessão expira se o banco continuar inacessível.
            continue


def run(run_id, engine):
    finished = threading.Event()
    heartbeat = threading.Thread(target=_heartbeat, args=(run_id, finished), daemon=True)
    heartbeat.start()
    control = ScanControl(cancelled=lambda: is_cancelled(run_id))
    result = {}

    def progress(text, current=0, total=0):
        with connection(write=True) as conn:
            conn.execute("""UPDATE scan_runs SET progress_text=CASE WHEN cancel_requested=1 THEN 'Cancelando busca...' ELSE ? END,
                current=?,total=? WHERE id=? AND state='running'""", (text, current, total, run_id))

    def result_updated(value):
        result.update(value)
        with connection(write=True) as conn:
            conn.execute("UPDATE scan_runs SET result=? WHERE id=? AND state='running'", (json.dumps(result), run_id))

    try:
        engine(control=control, progress_callback=progress, result_callback=result_updated)
        state = 'cancelled' if is_cancelled(run_id) else result.get('state', 'completed')
        error = 'Não foi possível concluir as consultas. Consulte os erros por fonte.' if state == 'failed' else None
        with connection(write=True) as conn:
            conn.execute("UPDATE scan_runs SET state=?,error=?,finished_at=?,result=? WHERE id=? AND state='running'",
                         (state, error, time.time(), json.dumps(result), run_id))
    except Exception as error:
        fail(run_id, f"Falha na busca: {type(error).__name__}")
    finally:
        finished.set()
        heartbeat.join(timeout=1)
        prune_history()


def fail(run_id, message):
    with connection(write=True) as conn:
        conn.execute("UPDATE scan_runs SET state='failed',error=?,progress_text=?,finished_at=? WHERE id=? AND state='running'",
                     (message, message, time.time(), run_id))


def prune_history(limit=HISTORY_LIMIT):
    """Mantém o diagnóstico recente sem crescimento ilimitado do SQLite."""
    with connection(write=True) as conn:
        conn.execute("""DELETE FROM scan_runs WHERE state NOT IN ('queued','running') AND id NOT IN
                     (SELECT id FROM scan_runs ORDER BY started_at DESC LIMIT ?)""", (limit,))


def run_next(engine):
    run_id = claim_next()
    if run_id is None:
        return False
    run(run_id, engine)
    return True


def _drain(engine):
    while True:
        # O timeout também recupera automaticamente leases órfãos mesmo quando
        # nenhum usuário está com a interface aberta para consultar o status.
        _worker_wakeup.wait(5)
        _worker_wakeup.clear()
        while run_next(engine):
            pass


def kick(engine):
    """Inicia um consumidor local; a fila permite retomada após reinicialização."""
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_drain, args=(engine,), daemon=True, name="buscavagas-scan-worker")
            _worker.start()
        _worker_wakeup.set()
        return _worker
