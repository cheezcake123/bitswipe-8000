# Shared HTTP helper.
#
# Each worker thread gets its own Session. That keeps connection pooling while
# avoiding cross-thread Session sharing when market/bootstrap fetches run in
# bounded parallel. Proxy environment variables stay ignored as before.
from __future__ import annotations

import os
import threading

import requests


def _default_timeout() -> float:
    try:
        return max(1.0, min(60.0, float(os.getenv("BITSWIPE_HTTP_TIMEOUT_SECONDS", "10"))))
    except (TypeError, ValueError):
        return 10.0


class _ThreadLocalSession:
    def __init__(self) -> None:
        self._local = threading.local()

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.trust_env = False
            self._local.session = session
        return session

    def request(self, method: str, url: str, **kwargs):
        kwargs.setdefault("timeout", _default_timeout())
        return self._session().request(method, url, **kwargs)

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs):
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs):
        return self.request("DELETE", url, **kwargs)


_session = _ThreadLocalSession()
