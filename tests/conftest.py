"""Test fixtures: a fake KeyFlint quota gateway and locally signed licenses (no network needed)."""

from __future__ import annotations

import base64
import json
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

_PRIV = ed25519.Ed25519PrivateKey.generate()
PUB = _PRIV.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def sign_license(tier: str = "indie") -> str:
    payload = json.dumps({"tier": tier, "features": ["core"], "issued_at": int(time.time()) - 5,
                          "license_id": secrets.token_hex(8)}, separators=(",", ":")).encode()
    return f"LIC-{tier.upper()}-{_b64(payload)}.{_b64(_PRIV.sign(payload))}"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(handler: type) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", _free_port()), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class FakeGateway:
    """The three KeyFlint quota endpoints this server uses, with idempotent debits and refunds."""

    def __init__(self) -> None:
        self.balances: dict[str, int] = {}
        self.debits: dict[tuple[str, str], int] = {}
        self.lock = threading.Lock()
        gw = self

        class H(BaseHTTPRequestHandler):
            def _send(self, status: int, body: dict) -> None:
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _unknown(self) -> None:
                self._send(404, {"error": {"code": "LICENSE_NOT_FOUND", "message": "Unknown license."}})

            def do_GET(self) -> None:  # noqa: N802
                key = (parse_qs(urlsplit(self.path).query).get("license_key") or [""])[0]
                if key not in gw.balances:
                    return self._unknown()
                self._send(200, {"data": {"balance": gw.balances[key]}})

            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                key, idem = body.get("license_key", ""), body.get("idempotency_key", "")
                with gw.lock:
                    if key not in gw.balances:
                        return self._unknown()
                    left = gw.balances[key]
                    if self.path.endswith("/deduct"):
                        need = int(body["credits"])
                        if (key, idem) not in gw.debits:
                            if need > left:
                                return self._send(402, {"error": {
                                    "code": "QUOTA_EXHAUSTED", "message": "No credits.",
                                    "details": {"requested_credits": need, "remaining_credits": left}}})
                            gw.balances[key] = left - need
                            gw.debits[(key, idem)] = need
                    elif self.path.endswith("/refund"):
                        gw.balances[key] += gw.debits.pop((key, idem), 0)
                    else:
                        return self._send(404, {"error": {"code": "NOT_FOUND", "message": self.path}})
                    self._send(200, {"data": {"remaining_balance": gw.balances[key]}})

            def log_message(self, *a: object) -> None:
                pass

        self.srv = _serve(H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def issue(self, credits: int = 1000, tier: str = "indie") -> dict:
        key = sign_license(tier)
        self.balances[key] = credits
        return {"license_key": key, "quota_credits": credits}


@pytest.fixture(scope="session")
def gateway() -> dict:
    gw = FakeGateway()
    yield {"url": gw.url,
           "issue": lambda email=None, tier="indie": gw.issue(25 if tier == "community" else 1000, tier)}
    gw.srv.shutdown()
