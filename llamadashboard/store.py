"""Session store: live slot state + rates (from /slots deltas), authoritative
history (from log), session aggregates (本次监控 semantics), optional jsonl dump."""

from __future__ import annotations

import json
import time
from collections import deque

from .config import Settings
from .health import HealthBoard
from .model import GpuSample, Profile, RequestRow, ServerFacts, SlotLive
from .sources.httpsrc import ServerDialect


class SessionStore:
    def __init__(self, settings: Settings, health: HealthBoard) -> None:
        self.settings = settings
        self.health = health
        # attach state
        self.facts: ServerFacts | None = None
        self.profile: Profile | None = None
        self.dialect: ServerDialect | None = None
        self.server_boot_ts: float | None = None
        # live
        self.slots: dict[int, SlotLive] = {}
        self.live_out_tps: float | None = None
        self.live_in_tps: float | None = None
        self.gpu: GpuSample | None = None
        self.gpu_extra: dict[str, float] = {}
        self.show_thermal_detail = False
        # session aggregates
        self.history: deque[RequestRow] = deque(maxlen=1000)   # newest first
        self.mtp_enabled: bool | None = None
        self._in_anchor: dict[tuple[int, int], tuple[int, float]] = {}    # (slot,task)->(proc0, t0)
        self._out_anchor: dict[tuple[int, int], tuple[int, float]] = {}   # (slot,task)->(gen0, t0)
        self._in_done: dict[tuple[int, int], float] = {}                  # frozen prompt t/s of request
        self._fresh_max: dict[tuple[int, int], int] = {}                  # monotone fresh_prompt
        self._first_kv_snap: dict[int, tuple[int, int]] = {}   # task -> (prompt, cache)
        self._dump_fh = None

    # ---------------- attach / detach ----------------

    def attach(self, facts: ServerFacts | None, profile: Profile | None) -> None:
        changed = (facts is None) != (self.facts is None) or \
                  (facts is not None and self.facts is not None and facts.pid != self.facts.pid)
        self.facts = facts
        self.profile = profile
        if changed:
            self.dialect = None
            self.slots.clear()
            self._in_anchor.clear()
            self._out_anchor.clear()
            self._in_done.clear()
            self._fresh_max.clear()
            self.live_out_tps = self.live_in_tps = None
            if facts is not None:
                self.server_boot_ts = _proc_create_time(facts.pid)
            else:
                self.server_boot_ts = None

    @property
    def attached(self) -> bool:
        return self.facts is not None

    @property
    def attach_mode(self) -> bool:
        return self.attached and self.profile is None

    def uptime_s(self, now: float | None = None) -> float | None:
        if self.server_boot_ts is None:
            return None
        return (now or time.time()) - self.server_boot_ts

    # ---------------- samples in ----------------

    def on_props(self, props: dict) -> None:
        if self.dialect is not None:
            self.dialect.build_info = props.get("build_info", self.dialect.build_info)
            self.dialect.model_alias = props.get("model_alias", self.dialect.model_alias)
            self.dialect.total_slots = props.get("total_slots", self.dialect.total_slots)

    def on_slots(self, raw: list[dict], now: float | None = None) -> None:
        now = now or time.time()
        new_slots: dict[int, SlotLive] = {}
        for d in raw:
            try:
                nt = (d.get("next_token") or [{}])[0]
                sl = SlotLive(
                    id=int(d.get("id", -1)), n_ctx=int(d.get("n_ctx", 0)),
                    is_processing=bool(d.get("is_processing")),
                    id_task=int(d.get("id_task", -1)),
                    n_prompt_tokens=int(d.get("n_prompt_tokens", 0)),
                    n_prompt_tokens_processed=int(d.get("n_prompt_tokens_processed", 0)),
                    n_prompt_tokens_cache=int(d.get("n_prompt_tokens_cache", 0)),
                    n_decoded=int(nt.get("n_decoded", 0)),
                    n_remain=int(nt.get("n_remain", -1)),
                    speculative=bool(d.get("speculative")),
                    ts=now,
                )
            except (TypeError, ValueError):
                continue
            key = (sl.id, sl.id_task)
            if not sl.is_processing:
                sl.phase = "idle"
            else:
                # NOTE (llama.cpp quirk, verified against the webui): n_prompt_tokens =
                # prompt.tokens.size() *keeps growing during decode* because generated
                # tokens are appended to the slot prompt. This turn's own prompt is
                # total minus what we generated; the tokens that must be evaluated are
                # that minus explicit cache hits. Field updates lag each other by up to
                # a decode window, so fresh_prompt is monotone (max) within a task.
                computed = max(sl.n_prompt_tokens - sl.n_decoded, sl.n_prompt_tokens_processed)
                fm = self._fresh_max.get(key)
                if fm is None or computed > fm:
                    self._fresh_max[key] = computed
                    fm = computed if fm is None else max(fm, computed)
                computed = max(computed, fm or 0)
                sl.fresh_prompt = computed
                sl.eval_total = max(1, computed - sl.n_prompt_tokens_cache)
                done = (sl.n_prompt_tokens_processed >= sl.eval_total
                        and sl.n_prompt_tokens_processed >= 64)   # ignore first-tick ramp
                sl.phase = "decode" if (sl.n_decoded > 0 or done) else "prompt"
            # rates over *active time only*: the window denominator advances
            # exclusively across ticks that showed progress, so a finished
            # evaluation freezes at its last real speed instead of decaying.
            if sl.phase == "prompt":
                a = self._in_anchor.get(key)
                if a is None:
                    self._in_anchor[key] = [sl.n_prompt_tokens_processed, now, 0.0,
                                            sl.n_prompt_tokens_processed]      # p0,last_t,acc,prev
                else:
                    p0, last_t, acc, prev = a
                    proc = sl.n_prompt_tokens_processed
                    if proc > prev:
                        acc += now - last_t
                    a[1] = now
                    a[2] = acc
                    a[3] = proc
                    if proc - p0 >= 64 and acc > 0:
                        sl.rate_in = (proc - p0) / acc
                    if proc >= sl.eval_total:             # evaluation done: freeze
                        if acc > 0 and proc > p0:
                            self._in_done[key] = (proc - p0) / acc
                        self._in_anchor.pop(key, None)
            elif sl.phase == "decode":
                ia = self._in_anchor.pop(key, None)
                if ia is not None:
                    p0, last_t, acc, prev = ia
                    proc = sl.n_prompt_tokens_processed
                    if proc > prev:                   # progress made on the flip tick
                        acc += now - last_t          # must still count its active time
                    if acc > 0 and proc > p0:
                        self._in_done[key] = (proc - p0) / acc
                sl.rate_in = self._in_done.get(key)
                a = self._out_anchor.get(key)
                if a is None:
                    self._out_anchor[key] = [sl.n_decoded, now, 0.0, sl.n_decoded]
                else:
                    g0, last_t, acc, prev = a
                    gen = sl.n_decoded
                    if gen > prev:
                        acc += now - last_t
                    a[1] = now
                    a[2] = acc
                    a[3] = gen
                    if gen - g0 > 16 and acc > 0:
                        sl.rate_out = (gen - g0) / acc
            new_slots[sl.id] = sl
            if sl.id_task >= 0 and sl.fresh_prompt > 0:
                prev = self._first_kv_snap.get(sl.id_task)
                cache = max(sl.n_prompt_tokens_cache, prev[1] if prev else 0)
                self._first_kv_snap[sl.id_task] = (max(prev[0] if prev else 0, sl.fresh_prompt), cache)
        # prune anchors of finished tasks
        active = {(s.id, s.id_task) for s in new_slots.values() if s.is_processing}
        for store_ in (self._in_anchor, self._out_anchor, self._in_done, self._fresh_max):
            for k in list(store_):
                if k not in active:
                    del store_[k]
        self.slots = new_slots
        cur = self.current_slot()
        self.live_in_tps = cur.rate_in if cur else None
        self.live_out_tps = cur.rate_out if cur else None

    def on_gpu(self, sample: GpuSample | None, extra: dict[str, float]) -> None:
        self.gpu = sample
        self.gpu_extra = extra

    # ---------------- history (log authoritative) ----------------

    def on_request_row(self, row: RequestRow) -> None:
        snap = self._first_kv_snap.get(row.task)
        if snap is not None and row.kv_prompt_tokens is None:
            row.kv_prompt_tokens, row.kv_cache_tokens = snap[0], snap[1]
        self.history.appendleft(row)
        if row.mtp_accept is not None:
            self.mtp_enabled = True
        if self.settings.dump_path is not None:
            try:
                if self._dump_fh is None:
                    self.settings.dump_path.parent.mkdir(parents=True, exist_ok=True)
                    self._dump_fh = self.settings.dump_path.open("a", encoding="utf-8")
                self._dump_fh.write(json.dumps(row_to_dict(row), ensure_ascii=False) + "\n")
                self._dump_fh.flush()
            except OSError:
                pass

    def set_mtp_evidence(self, enabled: bool | None) -> None:
        if enabled is not None:
            self.mtp_enabled = enabled

    # ---------------- aggregates ----------------

    def totals(self) -> dict:
        n_req = len(self.history)
        gen = sum(r.output_tokens for r in self.history)
        in_tok = sum(r.input_tokens for r in self.history)
        kv_c = sum(r.kv_cache_tokens or 0 for r in self.history)
        kv_p = sum(r.kv_prompt_tokens or 0 for r in self.history)
        out_secs = sum(r.total_ms / 1000 for r in self.history if r.out_tps > 0 and r.output_tokens)
        gen_secs = sum(r.output_tokens / r.out_tps for r in self.history if r.out_tps > 0)
        prompt_secs = sum(r.input_tokens / r.in_tps for r in self.history if r.in_tps > 0)
        return {
            "requests": n_req,
            "generated": gen,
            "input_tokens": in_tok,
            "kv_cum": (kv_c / kv_p) if kv_p else None,
            "avg_out_tps": (gen / gen_secs) if gen_secs else None,
            "avg_in_tps": (in_tok / prompt_secs) if prompt_secs else None,
            "wall_gen_secs": out_secs,
        }

    def current_slot(self) -> SlotLive | None:
        proc = [s for s in self.slots.values() if s.is_processing]
        if proc:
            return sorted(proc, key=lambda s: s.id)[0]
        return None

    def reset_session(self) -> None:
        self.history.clear()
        self._first_kv_snap.clear()
        self.mtp_enabled = None
        self.health.reset_session()

    def last_request(self) -> RequestRow | None:
        return self.history[0] if self.history else None


def row_to_dict(r: RequestRow) -> dict:
    return {
        "wall_ts": r.wall_ts, "slot": r.slot, "task": r.task,
        "input": r.input_tokens, "output": r.output_tokens,
        "in_tps": round(r.in_tps, 2), "out_tps": round(r.out_tps, 2),
        "total_ms": round(r.total_ms, 1),
        "mtp_accept": r.mtp_accept, "mtp_accepted": r.mtp_accepted, "mtp_generated": r.mtp_generated,
        "kv_cache": r.kv_cache_tokens, "kv_prompt": r.kv_prompt_tokens,
    }


def _proc_create_time(pid: int) -> float | None:
    try:
        import psutil
        return psutil.Process(pid).create_time()
    except Exception:  # noqa: BLE001
        return None
