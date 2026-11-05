"""Only KEYFLINT_LICENSE is needed: gateway, keyring and checkout have defaults."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from norma43_mcp import billing

KEYRING = "k1=" + "ab" * 32


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KEYFLINT_", "KEYFORGE_"))}
    monkeypatch.setattr(os, "environ", env)  # apply_defaults writes here; restored after the test
    monkeypatch.setenv("KEYFLINT_KEYRING_CACHE", str(tmp_path / "keyring.json"))
    return monkeypatch


def _serve():
    class H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = json.dumps({"data": {"keyring": KEYRING}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a: object) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_defaults_without_fetch(clean_env):
    clean_env.setenv("KEYFLINT_PUBLIC_KEYS", KEYRING)
    billing.apply_defaults()
    assert os.environ["KEYFLINT_URL"] == billing.DEFAULT_GATEWAY
    assert os.environ["KEYFLINT_CHECKOUT_URL"] == billing.DEFAULT_CHECKOUT


def test_keyring_fetched_then_cached(clean_env):
    srv = _serve()
    clean_env.setenv("KEYFLINT_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    billing.apply_defaults()
    assert os.environ["KEYFLINT_PUBLIC_KEYS"] == KEYRING
    srv.shutdown()
    srv.server_close()
    clean_env.delenv("KEYFLINT_PUBLIC_KEYS")
    billing.apply_defaults()  # gateway down: cached keyring
    assert os.environ["KEYFLINT_PUBLIC_KEYS"] == KEYRING


def test_offline_without_cache_leaves_keys_unset(clean_env):
    clean_env.setenv("KEYFLINT_URL", "http://127.0.0.1:9")
    billing.apply_defaults()
    assert "KEYFLINT_PUBLIC_KEYS" not in os.environ
