"""HTTP observation channel: /health (public), /props, /slots (bearer from proc
discovery). Attach-time probing yields a ServerDialect so version drift (b11223
vs master assumptions) is absorbed here and nowhere else."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from ..model import ServerFacts

EXPECTED_SLOT_KEYS = {
    "id", "n_ctx", "is_processing", "id_task",
    "n_prompt_tokens", "n_prompt_tokens_processed", "n_prompt_tokens_cache",
    "next_token",
}


@dataclass
class ServerDialect:
    props_ok: bool = False
    slots_ok: bool = False
    build_info: str | None = None
    model_alias: str | None = None
    model_path: str | None = None
    total_slots: int | None = None
    endpoint_slots: bool | None = None
    endpoint_metrics: bool | None = None
    default_n_ctx: int | None = None
    slot_keys: set[str] = field(default_factory=set)

    @property
    def missing_slot_keys(self) -> set[str]:
        return EXPECTED_SLOT_KEYS - self.slot_keys


class HttpSource:
    def __init__(self, facts: ServerFacts, client: httpx.AsyncClient | None = None) -> None:
        self.facts = facts
        self._client = client or httpx.AsyncClient(timeout=2.5)
        port = facts.port or 8080
        # server may bind 0.0.0.0; always probe via loopback
        self.base_url = f"http://127.0.0.1:{port}"
        headers = {}
        if facts.api_key:
            headers["Authorization"] = f"Bearer {facts.api_key}"
        self._headers = headers

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict[str, Any]:
        r = await self._client.get(self.base_url + "/health")  # public endpoint
        r.raise_for_status()
        return r.json()

    async def props(self) -> dict[str, Any]:
        r = await self._client.get(self.base_url + "/props", headers=self._headers)
        r.raise_for_status()
        return r.json()

    async def slots(self) -> list[dict[str, Any]]:
        r = await self._client.get(self.base_url + "/slots", headers=self._headers)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    async def probe_dialect(self) -> ServerDialect:
        d = ServerDialect()
        try:
            p = await self.props()
            d.props_ok = True
            d.build_info = p.get("build_info")
            d.model_alias = p.get("model_alias")
            d.model_path = p.get("model_path")
            d.total_slots = p.get("total_slots")
            d.endpoint_slots = p.get("endpoint_slots")
            d.endpoint_metrics = p.get("endpoint_metrics")
            dgs = p.get("default_generation_settings") or {}
            if isinstance(dgs.get("n_ctx"), int):
                d.default_n_ctx = dgs["n_ctx"]
        except (httpx.HTTPError, ValueError):
            pass
        try:
            s = await self.slots()
            if isinstance(s, list):
                d.slots_ok = True
                if s and isinstance(s[0], dict):
                    d.slot_keys = set(s[0].keys())
        except (httpx.HTTPError, ValueError):
            d.slots_ok = False
        return d
