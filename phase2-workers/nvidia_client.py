"""Rotating NVIDIA client — spreads load across every configured API key.

Reads NVIDIA_API_KEY, NVIDIA_API_KEY1, NVIDIA_API_KEY2, … and round-robins across
them. On a 429 (rate limit) it immediately tries the next key; only when every key
is throttled does it back off and wait. Drop-in replacement for an OpenAI client:
exposes .chat.completions.create(...) with the same signature.
"""
import itertools
import os
import time

from openai import OpenAI

BASE_URL = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")


def _keys() -> list:
    found, seen = [], set()
    for name in ("NVIDIA_API_KEY", "NVIDIA_API_KEY1", "NVIDIA_API_KEY2",
                 "NVIDIA_API_KEY3", "NVIDIA_API_KEY4"):
        v = (os.environ.get(name) or "").strip()
        if v and v not in seen:
            seen.add(v)
            found.append(v)
    if not found:
        raise RuntimeError("no NVIDIA API key found (set NVIDIA_API_KEY or NVIDIA_API_KEY1/2)")
    return found


def _is_rate_limit(exc) -> bool:
    """Retryable: per-key quota (429) AND endpoint saturation (503/502).

    NVIDIA's big hosted models return
      503 ResourceExhausted: Worker local total request limit reached (32/32)
    when the shared worker pool is full. That is transient and worth retrying —
    it is NOT a quota problem, so a different key does not help; only waiting does.
    """
    s = str(exc)
    return ("429" in s or "Too Many Requests" in s or "rate limit" in s.lower()
            or "503" in s or "ResourceExhausted" in s or "502" in s
            or "Bad Gateway" in s or "Service Unavailable" in s)


class _Completions:
    def __init__(self, parent):
        self._p = parent

    def create(self, **kwargs):
        return self._p._call(**kwargs)


class _Chat:
    def __init__(self, parent):
        self.completions = _Completions(parent)


class RotatingClient:
    def __init__(self, timeout=300.0):
        keys = _keys()
        self._clients = [OpenAI(base_url=BASE_URL, api_key=k,
                                timeout=timeout, max_retries=0) for k in keys]
        self._cycle = itertools.cycle(range(len(self._clients)))
        self.n_keys = len(self._clients)
        self.chat = _Chat(self)

    def _call(self, **kwargs):
        last_exc = None
        # sweep every key, then back off — endpoint saturation needs patience, not keys
        for round_no in range(6):
            for _ in range(self.n_keys):
                idx = next(self._cycle)
                try:
                    return self._clients[idx].chat.completions.create(**kwargs)
                except Exception as exc:
                    last_exc = exc
                    if _is_rate_limit(exc):
                        continue          # next key / next round
                    raise                 # real error — surface it
            wait = min(20 * (round_no + 1), 90)
            print(f"    endpoint busy (all {self.n_keys} key(s)) — waiting {wait}s…", flush=True)
            time.sleep(wait)
        raise last_exc


def get_client(timeout=300.0) -> RotatingClient:
    return RotatingClient(timeout=timeout)
