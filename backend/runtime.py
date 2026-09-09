"""Controle de uma busca e diagnóstico por fonte, isolados por execução."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from threading import Event
import time
import requests


class ScanCancelled(Exception):
    pass


@dataclass
class ScanControl:
    event: Event = field(default_factory=Event)
    cancelled: object = None

    def check(self):
        if self.event.is_set() or (self.cancelled and self.cancelled()):
            self.event.set()
            raise ScanCancelled()

    def wait(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check()
            self.event.wait(min(0.1, max(0, deadline - time.monotonic())))
        self.check()


@dataclass
class SourceReport:
    queries: int = 0
    requests: int = 0
    successful_requests: int = 0
    collected: int = 0
    errors: list = field(default_factory=list)


@dataclass
class ScanResult:
    matched: int = 0
    evaluated: int = 0
    llm_failures: int = 0
    duplicates: int = 0
    filtered: int = 0
    state: str = "completed"
    sources: dict = field(default_factory=dict)

    def as_dict(self):
        return asdict(self)


_control = ContextVar("scan_control", default=None)
_settings = ContextVar("scan_settings", default=None)
_source = ContextVar("source_report", default=None)


@contextmanager
def execution(control, snapshot=None):
    token = _control.set(control)
    settings_token = _settings.set(snapshot)
    try:
        yield
    finally:
        _control.reset(token)
        _settings.reset(settings_token)


@contextmanager
def collection(report):
    token = _source.set(report)
    try:
        yield
    finally:
        _source.reset(token)


def checkpoint():
    control = _control.get()
    if control:
        control.check()


def pause(seconds):
    control = _control.get()
    if control:
        control.wait(seconds)
    else:
        time.sleep(seconds)


class SourceError(Exception):
    pass


def record_failure(error):
    report = _source.get()
    if report is not None and not isinstance(error, SourceError):
        # Limite de mensagens sem perder o contador total de requisições.
        if len(report.errors) < 30:
            report.errors.append(str(error)[:180])


def source_get(url, **kwargs):
    checkpoint()
    report = _source.get()
    if report is not None:
        report.requests += 1
    try:
        response = requests.get(url, **kwargs)
    except requests.RequestException as error:
        record_failure(type(error).__name__)
        checkpoint()
        raise SourceError(type(error).__name__) from error
    checkpoint()
    if response.status_code != 200:
        message = f"HTTP {response.status_code}"
        record_failure(message)
        raise SourceError(message)
    if report is not None:
        report.successful_requests += 1
    return response


def settings():
    from backend.config import runtime_snapshot
    return _settings.get() or runtime_snapshot()
