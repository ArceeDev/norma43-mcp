"""Per-call credit billing on top of keyflint_verify.mcp.KeyFlint.

stdio: the license comes from KEYFLINT_LICENSE (one buyer per process).
HTTP:  the license comes from each request (``Authorization: Bearer LIC-...`` or
``X-KeyFlint-License``), so one hosted server bills many buyers.
"""

from __future__ import annotations

import asyncio
import os
import threading
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Mapping, Optional

from keyflint_verify.mcp import LICENSE_INVALID, KeyFlint, LicenseError

LICENSE_HEADERS = ("x-license-key", "authorization")
_MAX_CLIENTS = 1024


def license_from_headers(headers: Optional[Mapping[str, str]]) -> Optional[str]:
    if not headers:
        return None
    lower = {k.lower(): v for k, v in headers.items()}
    raw = lower.get("x-license-key") or ""
    if not raw:
        auth = lower.get("authorization") or ""
        if auth[:7].lower() == "bearer ":
            raw = auth[7:]
    raw = raw.strip()
    return raw or None


class Billing:
    """Resolves the buyer's license and debits credits around a tool body."""

    def __init__(self, *, hosted: bool = False, **kf_kwargs: Any) -> None:
        self.hosted = hosted
        self._kf_kwargs = kf_kwargs
        self._env_kf = KeyFlint(**kf_kwargs)
        self._clients: "OrderedDict[str, KeyFlint]" = OrderedDict()
        self._lock = threading.Lock()

    @property
    def checkout_url(self) -> Optional[str]:
        return self._env_kf.checkout_url

    def client(self, headers: Optional[Mapping[str, str]] = None) -> KeyFlint:
        key = license_from_headers(headers)
        if key is None:
            if self.hosted:
                text = "Send your KeyFlint license as 'Authorization: Bearer LIC-...'."
                if self.checkout_url:
                    text += f" Get one: {self.checkout_url}"
                raise LicenseError(LICENSE_INVALID, text, {"reason": "ERR_EMPTY_TOKEN",
                                                           "checkout_url": self.checkout_url})
            return self._env_kf
        with self._lock:
            kf = self._clients.get(key)
            if kf is None:
                # Hosted: server clock is trusted, no node lock (the buyer's machine is not ours).
                kf = KeyFlint(license_key=key, machine_id=None, clock_guard=False,
                              public_keys=self._env_kf.public_keys, gateway_url=self._env_kf.gateway_url,
                              checkout_url=self._env_kf.checkout_url, timeout=self._env_kf.timeout)
                self._clients[key] = kf
                if len(self._clients) > _MAX_CLIENTS:
                    self._clients.popitem(last=False)
            else:
                self._clients.move_to_end(key)
            return kf

    @asynccontextmanager
    async def charge(self, credits: int, tool: str, headers: Optional[Mapping[str, str]] = None,
                     features: Optional[list[str]] = None) -> AsyncIterator[dict[str, Any]]:
        """Offline license check, debit, run body; refund if the body raises."""
        kf = self.client(headers)
        kf.check(features)
        idem = f"n43-{uuid.uuid4().hex}"
        receipt = await asyncio.to_thread(kf.deduct, credits, idempotency_key=idem, event_type=f"mcp:{tool}")
        try:
            yield receipt
        except BaseException:
            await asyncio.to_thread(kf.refund, idem, "tool_error")
            raise

    async def balance(self, headers: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
        import json
        import urllib.parse
        import urllib.request

        kf = self.client(headers)
        kf.check()
        if not kf.gateway_url:
            raise LicenseError(-32063, "KEYFLINT_URL is not set.", {"reason": "GATEWAY_NOT_CONFIGURED"})
        url = f"{kf.gateway_url}/api/v1/quota/balance?" + urllib.parse.urlencode({"license_key": kf.license_key})

        def _get() -> dict[str, Any]:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=kf.timeout) as resp:  # noqa: S310 - configured URL
                data = json.loads(resp.read().decode("utf-8") or "{}")
                return dict(data.get("data", data))

        return await asyncio.to_thread(_get)


def from_env() -> Billing:
    return Billing(hosted=os.environ.get("NORMA43_TRANSPORT", "stdio") != "stdio")
