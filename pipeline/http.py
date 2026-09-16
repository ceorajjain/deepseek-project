"""Shared HTTP client with rate limiting + retry + circuit breaker."""
import ssl
import threading
import time
import urllib.parse
import urllib.request

from . import config
from .logger import get_logger

log = get_logger("http")

_lock = threading.Lock()
_last_request = 0.0
_failures = {}  # host -> consecutive failures
_breaker_until = {}  # host -> cooldown until timestamp


def _wait(host):
    with _lock:
        # circuit breaker
        if host in _breaker_until and time.time() < _breaker_until[host]:
            raise RuntimeError(f"circuit open for {host} until {_breaker_until[host]:.0f}")
        # rate limit
        global _last_request
        wait = config.REQUEST_DELAY_SECONDS - (time.time() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.time()


def _success(host):
    with _lock:
        _failures[host] = 0
        _breaker_until.pop(host, None)


def _failure(host):
    with _lock:
        _failures[host] = _failures.get(host, 0) + 1
        if _failures[host] >= config.CIRCUIT_FAILURES:
            _breaker_until[host] = time.time() + config.CIRCUIT_COOLDOWN_SECONDS
            log.warning("circuit breaker open for %s (cooldown %ss)", host,
                        config.CIRCUIT_COOLDOWN_SECONDS)


ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def get(url, headers=None, timeout=90):
    host = _host(url)
    _wait(host)
    for attempt in range(config.RETRIES):
        try:
            req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                data = r.read()
            _success(host)
            return data
        except Exception as e:
            _failure(host)
            if attempt == config.RETRIES - 1:
                raise
            time.sleep(2 ** attempt)


def _host(url):
    return (urllib.parse.urlparse(url).netloc or "").lower()
