"""Explicit trusted construction for the isolated demo; no environment reads."""
import json
import logging
import re
import ssl
import sqlite3
from pathlib import Path
from contextlib import closing
import threading
import time
import httpx
from openai import OpenAI
from app.services.demo_budget_migration import prepare_budget
from app.services.deepseek_planner import DEEPSEEK_BASE_URL, REQUEST_TIMEOUT_SECONDS
from app.services.demo_mock_online import MockNaturalPlanner, DIAGNOSTIC_STAGES

MAX_HTTP_BYTES = 65536
MAX_MODEL_REQUESTS = 100
logger = logging.getLogger('jinbi.demo.diagnostics')


def _is_tls_error(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, ssl.SSLError): return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


class DurableBudget:
    """A local, atomic lifetime budget; process restart never resets it."""
    def __init__(self, path):
        self.path = Path(path)

    def status(self):
        """Report usage and limit without charging a request."""
        with closing(sqlite3.connect(str(self.path), timeout=10, isolation_level=None)) as db:
            db.execute('BEGIN IMMEDIATE')
            used = prepare_budget(db, MAX_MODEL_REQUESTS)
            db.commit()
            return used, MAX_MODEL_REQUESTS

    def consume(self):
        # Local isolated SQLite only. Commit the attempt before any network I/O.
        with closing(sqlite3.connect(str(self.path), timeout=10, isolation_level=None)) as db:
            db.execute('BEGIN IMMEDIATE')
            used = prepare_budget(db, MAX_MODEL_REQUESTS)
            if used >= MAX_MODEL_REQUESTS:
                db.commit()
                return None
            used += 1
            db.execute('UPDATE budget SET used=? WHERE id=1', (used,))
            db.commit()
            return used


REAL_BUDGET_PATH = Path(__file__).resolve().parents[2] / 'evidence' / 'd4_r3_request_budget.sqlite3'


class RestrictedTransport(httpx.BaseTransport):
    def __init__(self, inner, budget=None):
        self.inner = inner
        self.budget = budget
        self.count = 0
        self.last = None
        self._lock = threading.Lock()

    def handle_request(self, request):
        if request.method != 'POST' or str(request.url) != DEEPSEEK_BASE_URL + '/chat/completions':
            raise httpx.RequestError('destination denied', request=request)
        with self._lock:
            if self.count >= MAX_MODEL_REQUESTS:
                self.last = {'request_count': self.count, 'transport_category': 'request_limit',
                             'http_status': None, 'encoding': None, 'duration_ms': 0}
                raise httpx.RequestError('request limit reached', request=request)
            if self.budget is not None:
                try:
                    number = self.budget.consume()
                except (sqlite3.Error, OSError):
                    self.last = {'request_count': self.count, 'transport_category': 'budget_error',
                                 'http_status': None, 'encoding': None, 'duration_ms': 0}
                    raise
                if number is None:
                    self.count = MAX_MODEL_REQUESTS
                    self.last = {'request_count': self.count, 'transport_category': 'request_limit',
                                 'http_status': None, 'encoding': None, 'duration_ms': 0}
                    raise httpx.RequestError('request limit reached', request=request)
                self.count = number
            else:
                self.count += 1
                number = self.count
        started = time.monotonic()
        record = {'request_count': number, 'transport_category': 'network_error',
                  'http_status': None, 'encoding': None, 'duration_ms': 0}
        response = None
        try:
            response = self.inner.handle_request(request)
            record['http_status'] = response.status_code
            raw_encoding = response.headers.get('content-encoding', 'identity').lower()
            record['encoding'] = 'identity' if raw_encoding == 'identity' else 'other'
            if 300 <= response.status_code < 400:
                record['transport_category'] = 'redirect_denied'
                raise httpx.RequestError('redirect denied', request=request)
            if raw_encoding != 'identity':
                record['transport_category'] = 'encoding_denied'
                raise httpx.RequestError('encoded response denied', request=request)
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > MAX_HTTP_BYTES:
                    record['transport_category'] = 'response_too_large'
                    raise httpx.RequestError('response too large', request=request)
            headers = dict(response.headers)
            headers.pop('content-encoding', None)
            headers.pop('content-length', None)
            record['transport_category'] = 'http_response'
            return httpx.Response(response.status_code, headers=headers, content=bytes(content), request=request)
        except Exception as exc:
            if record['transport_category'] == 'network_error':
                if isinstance(exc, httpx.TimeoutException): record['transport_category'] = 'timeout'
                elif _is_tls_error(exc): record['transport_category'] = 'tls_error'
            raise
        finally:
            if response is not None: response.close()
            record['duration_ms'] = round((time.monotonic()-started)*1000)
            with self._lock: self.last = record

    def close(self): self.inner.close()


class NaturalDeepSeekPlanner(MockNaturalPlanner):
    """The caller supplies the secret directly; test transport must be MockTransport."""
    def __init__(self, *, api_key, model='deepseek-flash', test_transport=None):
        if not isinstance(api_key,str) or not api_key.strip(): raise ValueError('explicit key required')
        if not isinstance(model,str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,80}',model): raise ValueError('invalid model')
        if test_transport is not None and type(test_transport) is not httpx.MockTransport:
            raise ValueError('mock transport required for tests')
        transport = test_transport if test_transport is not None else httpx.HTTPTransport(verify=True, trust_env=False, retries=0)
        budget = DurableBudget(REAL_BUDGET_PATH) if test_transport is None else None
        self.transport = RestrictedTransport(transport, budget=budget)
        self._check_lock = threading.Lock()
        client = httpx.Client(transport=self.transport, trust_env=False, follow_redirects=False, timeout=REQUEST_TIMEOUT_SECONDS, headers={'Accept-Encoding':'identity'})
        self.model=model
        self.client=OpenAI(api_key=api_key,base_url=DEEPSEEK_BASE_URL,http_client=client,max_retries=0,timeout=REQUEST_TIMEOUT_SECONDS)

    def check(self, query, *, confirmed=None):
        with self._check_lock:
            self.transport.last = None
            return super().check(query, confirmed=confirmed)

    def _record_diagnostic(self, diagnostic):
        # Bounded, allowlisted metadata only; never output request/response text,
        # authentication headers, exception messages, or the model's question.
        transport = self.transport.last
        if transport is not None:
            diagnostic.update(transport)
            if diagnostic['category'] == 'request_failed':
                status = transport['http_status']
                diagnostic['category'] = ('http_error' if transport['transport_category'] == 'http_response' and status is not None and status >= 400
                                          else transport['transport_category'])
        else:
            diagnostic.update(request_count=self.transport.count, transport_category='not_sent',
                              http_status=None, encoding=None, duration_ms=0)
        diagnostic['stage'] = DIAGNOSTIC_STAGES.get(diagnostic['category'], 'planner')
        logger.warning('demo_diagnostic %s', json.dumps(diagnostic, ensure_ascii=True, sort_keys=True))

    def close(self): self.client.close()
