"""Tolerant parser registry for llama-server run logs.

Extractors recognize per-request settlement blocks; family keyword present but
extraction failed => counted as a parse failure (visible in source health).
Unknown lines are silently ignored (not failures)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..model import RequestRow

_TS_PREFIX = r"(?:\d+\.)*\d+\.\d+\.\d+\.\d+\s+\w\s+"  # e.g. "0.41.912.708 I "

_RE_LAUNCH = re.compile(
    r"slot\s+launch_slot_:\s+id\s+(\d+)\s*\|\s*task\s+(\d+)\s*\|\s*processing task"
)
_RE_RELEASE = re.compile(
    r"slot\s+release:\s*id\s+(\d+)\s*\|\s*task\s+(\d+)\s*\|\s*stop processing:\s*n_tokens\s*=\s*(\d+)"
)
_RE_PROMPT = re.compile(
    r"slot\s+print_timing:\s+id\s+(\d+)\s*\|\s*task\s+(\d+)\s*\|\s*prompt eval time\s*=\s*([\d.,]+)\s*ms\s*/\s*([\d.,]+)\s*tokens"
    r"(?:.*?,\s*([\d.,]+)\s*tokens per second)?"
)
_RE_EVAL = re.compile(
    r"slot\s+print_timing:\s+id\s+(\d+)\s*\|\s*task\s+(\d+)\s*\|\s*eval time\s*=\s*([\d.,]+)\s*ms\s*/\s*([\d.,]+)\s*tokens"
    r"(?:.*?,\s*([\d.,]+)\s*tokens per second)?"
)
_RE_TOTAL = re.compile(
    r"slot\s+print_timing:\s+id\s+(\d+)\s*\|\s*task\s+(\d+)\s*\|\s*total time\s*=\s*([\d.,]+)\s*ms\s*/\s*([\d.,]+)\s*tokens"
)
_RE_DRAFT = re.compile(
    r"slot\s+print_timing:\s+id\s+(\d+)\s*\|\s*task\s+(\d+)\s*\|\s*draft acceptance\s*=\s*([\d.,]+)"
    r"\s*\(\s*(\d+)\s*accepted\s*/\s*(\d+)\s*generated\)"
)

_RE_MTP_INIT = re.compile(r"creating MTP draft context")
_RE_INIT = re.compile(r"llama_server:\s*initializing")
_RE_SLOTS = re.compile(r"n_slots\s*=\s*(\d+),\s*n_ctx_slot\s*=\s*(\d+)")
_RE_MODEL = re.compile(r"loading model\s+'([^']+)'")
_RE_LISTEN = re.compile(r"listening on\s+(http://\S+)")

# (keyword marking extractor family, compiled extractor)
_BODY_EXTRACTORS: list[tuple[str, re.Pattern]] = [
    ("prompt eval time", _RE_PROMPT),
    ("eval time", _RE_EVAL),
    ("total time", _RE_TOTAL),
    ("draft acceptance", _RE_DRAFT),
]
_FAMILY_KEYS = [k for k, _ in _BODY_EXTRACTORS]


def _f(m: re.Match, i: int) -> float:
    return float(m.group(i).replace(",", "")) if m.group(i) else 0.0


@dataclass
class _Record:
    slot: int
    task: int
    prompt_ms: float = 0.0
    prompt_tok: int = 0
    prompt_tps: float = 0.0
    eval_ms: float = 0.0
    eval_tok: int = 0
    eval_tps: float = 0.0
    total_ms: float = 0.0
    mtp: float | None = None
    mtp_accepted: int | None = None
    mtp_generated: int | None = None


@dataclass
class ParseResult:
    rows: list[RequestRow] = field(default_factory=list)
    failures: int = 0

class LineParser:
    def __init__(self) -> None:
        self._records: dict[tuple[int, int], _Record] = {}
        self._released: set[tuple[int, int]] = set()
        self.mtp_enabled: bool | None = None   # None until evidence
        self.startup: dict[str, object] = {}
        self.failures = 0

    def feed(self, line: str, now: float) -> RequestRow | None:
        """Feed one log line; return a completed RequestRow on release, else None."""
        if "initializing" in line and _RE_INIT.search(line):
            self.reset_records()
            self.startup = {}
            return None
        # cheap family pre-screen
        if "slot" not in line:
            self._startup(line)
            return None
        row: RequestRow | None = None
        if m := _RE_LAUNCH.search(line):
            key = (int(m.group(1)), int(m.group(2)))
            self._records[key] = _Record(*key)
            self._released.discard(key)
        elif m := _RE_RELEASE.search(line):
            key = (int(m.group(1)), int(m.group(2)))
            rec = self._records.pop(key, None)
            if rec is not None and key not in self._released:
                self._released.add(key)
                cap = int(m.group(3))               # prompt(full) + generated at release
                full_prompt = cap - rec.eval_tok if cap >= rec.eval_tok else rec.prompt_tok
                derived_cache = max(0, full_prompt - rec.prompt_tok)
                row = RequestRow(
                    wall_ts=now, slot=key[0], task=key[1],
                    input_tokens=full_prompt, output_tokens=rec.eval_tok,
                    in_tps=rec.prompt_tps, out_tps=rec.eval_tps,
                    total_ms=rec.total_ms,
                    mtp_accept=rec.mtp, mtp_accepted=rec.mtp_accepted,
                    mtp_generated=rec.mtp_generated,
                    kv_prompt_tokens=full_prompt if full_prompt else None,
                    kv_cache_tokens=derived_cache if full_prompt else None,
                )
                if row.input_tokens == 0 and row.output_tokens == 0:
                    row = None  # task released without timing data (cancel/error): drop silently
        else:
            self._bodies(line)
        self._startup(line)
        return row

    def _bodies(self, line: str) -> None:
        for key_word, rx in _BODY_EXTRACTORS:
            if key_word not in line:
                continue
            m = rx.search(line)
            if not m:
                self.failures += 1
                return
            key = (int(m.group(1)), int(m.group(2)))
            rec = self._records.get(key)
            if rec is None:
                return  # mid-stream attach: no record to attach to; not a failure
            g = m.groups()
            if key_word == "prompt eval time":
                rec.prompt_ms, rec.prompt_tok, rec.prompt_tps = _f(m, 3), int(_f(m, 4)), _f(m, 5)
            elif key_word == "eval time":
                rec.eval_ms, rec.eval_tok, rec.eval_tps = _f(m, 3), int(_f(m, 4)), _f(m, 5)
            elif key_word == "total time":
                rec.total_ms = _f(m, 3)
            elif key_word == "draft acceptance":
                rec.mtp = _f(m, 3)
                rec.mtp_accepted, rec.mtp_generated = int(g[3]), int(g[4])
                self.mtp_enabled = True
            return

    def _startup(self, line: str) -> None:
        if _RE_MTP_INIT.search(line):
            self.mtp_enabled = True
        if m := _RE_SLOTS.search(line):
            self.startup["n_slots"], self.startup["n_ctx_slot"] = int(m.group(1)), int(m.group(2))
        if m := _RE_MODEL.search(line):
            self.startup["model"] = m.group(1)
        if m := _RE_LISTEN.search(line):
            self.startup["listen"] = m.group(1)

    def reset_records(self) -> None:
        self._records.clear()
        self._released.clear()
