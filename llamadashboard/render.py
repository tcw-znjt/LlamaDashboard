"""Pure renderable builders (Rich) shared by the TUI - no Textual imports here,
so layout content can be unit-tested off-screen."""

from __future__ import annotations

import time

from rich.console import Group
from rich.table import Table
from rich.text import Text

from .health import HealthBoard
from .store import SessionStore

BAR_W = 22


def fmt_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _num(v: float | None, unit: str = "", nd: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:.{nd}f}{unit}"


def _pct(v: float | None) -> str:
    return f"{v * 100:.1f}%" if v is not None else "—"


def _bar(pct: float | None, color: str) -> Text:
    if pct is None:
        return Text("—")
    n = max(0, min(BAR_W, round(pct * BAR_W)))
    return Text("█" * n + "░" * (BAR_W - n), style=color)


def _pct_color(p: float) -> str:
    return "red" if p >= 0.9 else ("yellow" if p >= 0.7 else "green")


def header_text(store: SessionStore) -> Text:
    t = Text()
    f, d = store.facts, store.dialect
    if f is None:
        t.append(" llama.cpp 监控 ", style="bold cyan")
        t.append(" 待机:未发现 llama-server(按 m 从 profile 启动)")
        return t
    model = ""
    if f.model_path:
        model = f.model_path.replace("\\", "/").rsplit("/", 1)[-1]
        model = model[:-5] if model.endswith(".gguf") else model
    ctx = f.n_ctx or (d.default_n_ctx if d else None)
    if ctx is None and store.slots:
        ctx = next(iter(store.slots.values())).n_ctx
    slots = (d.total_slots if d else None) or f.n_parallel
    port = f.port or "—"
    build = d.build_info if d else "—"
    t.append(" llama.cpp 监控 ", style="bold cyan")
    t.append(f"模型 ", style="dim")
    t.append(model or (d.model_alias if d else "") or "—", style="bold")
    t.append(f"  ctx ", style="dim"); t.append(f"{ctx if ctx else '—'}")
    t.append("  槽位 ", style="dim"); t.append(f"{slots if slots else '—'}")
    t.append("  端口 ", style="dim"); t.append(f"{port}")
    t.append("  运行 ", style="dim"); t.append(fmt_uptime(store.uptime_s()), style="bold")
    t.append("  build ", style="dim"); t.append(build or "—")
    if store.attach_mode:
        t.append("  [attach]", style="magenta")
    return t


def metrics_text(store: SessionStore) -> Table:
    tb = Table.grid(padding=(0, 3))
    tot = store.totals()
    cur = store.current_slot()
    last = store.last_request()
    out_tps = store.live_out_tps or (last.out_tps if last else None)
    in_tps = store.live_in_tps or (last.in_tps if last else None)
    # KV: 累计 + 本次(当前请求优先)
    kv_cum = tot["kv_cum"]
    kv_cur: float | None = None
    if cur is not None and cur.fresh_prompt:
        kv_cur = cur.n_prompt_tokens_cache / cur.fresh_prompt
    elif (last := store.last_request()) is not None and last.kv_ratio is not None:
        kv_cur = last.kv_ratio
    mtp_val: float | None = None
    gen_w = 0
    num = 0.0
    for r in store.history:
        if r.mtp_accept is not None and r.mtp_generated:
            num += r.mtp_accept * r.mtp_generated
            gen_w += r.mtp_generated
    mtp_val = num / gen_w if gen_w else None
    mtp_on = bool(store.facts and store.facts.mtp) or (cur is not None and cur.speculative)

    cells: list[tuple[str, Text, Text]] = []
    v = Text(_num(out_tps, " t/s"), style="bold green") if out_tps else Text("—")
    cells.append(("输出速度", v, Text(f"运行均值 {_num(tot['avg_out_tps'], '', 1)}", style="dim")))
    v = Text(_num(in_tps, " t/s"), style="bold cyan") if in_tps else Text("—")
    cells.append(("输入速度", v, Text(f"运行均值 {_num(tot['avg_in_tps'], '', 1)}", style="dim")))
    cells.append(("KV 命中率", Text(_pct(kv_cum), style="bold magenta"),
                  Text(f"本次 {_pct(kv_cur)}", style="dim")))
    cells.append(("MTP 接受率",
                  Text(_pct(mtp_val), style="bold yellow") if mtp_val is not None else Text("—"),
                  Text("未启用" if not mtp_on else "本次监控", style="dim")))
    cells.append(("累计请求", Text(f"{tot['requests']}", style="bold"), Text("本次监控", style="dim")))
    cells.append(("累计生成", Text(f"{tot['generated']}", style="bold blue"), Text("输出 token", style="dim")))

    tb.add_row(*[lbl for lbl, _, _ in cells], style="dim")
    tb.add_row(*[v for _, v, _ in cells])
    tb.add_row(*[s for _, _, s in cells])
    return tb


def gpu_text(store: SessionStore, detail: bool):
    g = store.gpu
    if g is None:
        return Text("设备监控不可用", style="dim")
    # name is its own full-width line; keeping it out of the grid prevents it
    # from inflating the label column (which would gap out label↔bar alignment)
    name = Text(f"{g.name}" + (f"  {g.mem_total_mib / 1024:.0f}GB" if g.mem_total_mib else ""), style="bold")
    tb = Table.grid(padding=(0, 1))

    def row(label: str, bar: Text, value: Text):
        tb.add_row(Text(label, style="dim"), bar, value)

    if g.mem_total_mib and g.mem_used_mib is not None:
        p = g.mem_used_mib / g.mem_total_mib
        row("显存", _bar(p, _pct_color(p)),
            Text(f"{g.mem_used_mib / 1024:.1f}/{g.mem_total_mib / 1024:.1f} GB {p * 100:.0f}%"))
    if g.util_mem is not None:
        row("带宽", _bar(g.util_mem / 100, _pct_color(g.util_mem / 100)), Text(f"{g.util_mem}%"))
    if g.power_w is not None:
        p = g.power_w / g.power_limit_w if g.power_limit_w else None
        pw_text = Text(f"{g.power_w:.0f}/{g.power_limit_w:.0f} W" if g.power_limit_w else f"{g.power_w:.0f} W")
        row("功率", _bar(p, _pct_color(p)) if p is not None else Text("—"), pw_text)
    ps = g.pstate if g.pstate and str(g.pstate).startswith("P") else (f"P{g.pstate}" if g.pstate else "—")
    row("频率", Text(f"核心 {g.clk_core if g.clk_core is not None else '—'} MHz"),
        Text(f"显存 {g.clk_mem if g.clk_mem is not None else '—'} MHz  {ps}"
             + (f"  {g.throttle}" if g.throttle else ""),
             style="yellow" if g.throttle and "空闲" not in g.throttle else ""))
    extra = store.gpu_extra
    t1 = Text(f"GPU {g.temp_c if g.temp_c is not None else '—'} C")
    if g.slowdown_c is not None and g.temp_c is not None:
        t1.append(f" (余量 {g.slowdown_c - g.temp_c} C)")
    t2 = Text(f"风扇 {g.fan_pct if g.fan_pct is not None else '—'}%" if g.fan_pct is not None else "风扇 —")
    t2.append(f"   显存 {extra['显存']:.0f} C" if "显存" in extra else "   显存 —")
    t2.append(f"   热点 {extra['热点']:.0f} C" if "热点" in extra else "   热点 —")
    row("温度", t1, t2)
    if detail:
        zones = "    ".join(f"{k} {v:.0f} C" for k, v in extra.items() if k.startswith("区"))
        tb.add_row(Text("分区", style="dim"), Text(zones or "驱动不提供(—)", style="dim"), Text(""))
    return Group(name, tb)


def live_text(store: SessionStore) -> Table:
    tb = Table.grid(padding=(0, 1))
    cur = store.current_slot()
    if store.facts is None:
        tb.add_row(Text("待机", style="dim"))
        return tb
    if cur is None:
        tb.add_row(Text("空闲", style="bold green"))
        last = store.last_request()
        if last:
            tb.add_row(Text(
                f"上次请求  输入 {last.input_tokens / 1000:.1f}k 输出 {last.output_tokens} "
                f"{last.out_tps:.1f} t/s {last.total_ms / 1000:.1f}s", style="dim"))
        return tb
    phase = {"prompt": "处理 prompt", "decode": "解码 decode"}.get(cur.phase, cur.phase)
    tb.add_row(Text(f"槽 {cur.id}  ", style="bold") + Text(f"{phase}", style="bold green"))
    if cur.phase == "prompt":
        tb.add_row(Text(f"处理输入 {cur.n_prompt_tokens_processed}/{cur.eval_total} tok"
                        + (f"  输入速度 {cur.rate_in:.0f} t/s" if cur.rate_in else "")))
    else:
        tb.add_row(Text(f"已生成 {cur.n_decoded} tok   剩余 {('不限' if cur.n_remain < 0 else cur.n_remain)}"))
        rate = cur.rate_out if cur.rate_out is not None else store.live_out_tps
        tb.add_row(Text("输出速度 ") + Text(_num(rate, " t/s"), style="bold green"))
    tb.add_row(Text(f"本次输入 {cur.fresh_prompt or cur.n_prompt_tokens} tok (缓存命中 {cur.n_prompt_tokens_cache})"))
    peak = cur.n_prompt_tokens              # already includes appended generation
    if cur.n_ctx:
        tb.add_row(Text(f"上下文峰值 {peak / 1000:.1f}k / {cur.n_ctx / 1000:.1f}k ({peak / cur.n_ctx * 100:.0f}%)"))
    return tb


def history_text(store: SessionStore, max_rows: int = 10) -> Table:
    tb = Table(box=None, pad_edge=False, expand=False)
    tb.title = None
    for col, w in (("时间", 9), ("槽", 2), ("输入", 8), ("输出", 6), ("输入t/s", 8),
                   ("输出t/s", 7), ("KV", 7), ("MTP", 7), ("耗时ms", 8)):
        tb.add_column(col, style="dim", width=w, justify="right")
    for r in list(store.history)[:max_rows]:
        kv = f"{r.kv_ratio * 100:.1f}%" if r.kv_ratio is not None else "—"
        mtp = f"{r.mtp_accept * 100:.1f}%" if r.mtp_accept is not None else "—"
        tb.add_row(
            time.strftime("%H:%M:%S", time.localtime(r.wall_ts)),
            str(r.slot), f"{r.input_tokens}", f"{r.output_tokens}",
            f"{r.in_tps:.1f}", f"{r.out_tps:.1f}", kv, mtp, f"{r.total_ms:.0f}",
        )
    if not store.history:
        tb.add_row("—", "", "", "", "", "", "", "", "")
    return tb


def status_text(store: SessionStore, health: HealthBoard, interval: float,
                paused: bool, now: float | None = None) -> Text:
    age = health.data_age(now)
    s, f = health.totals()
    vec = "/".join(str(v) for v in health.vector(now))
    t = Text()
    t.append(f"数据 {age:.0f}s 前" if age is not None else "数据 尚无", style="dim")
    t.append(f"   采样成功 {s} / 失败 {f}")
    t.append(f"   采样 {interval:.0f}s   src {vec}", style="dim")
    if paused:
        t.append("   [已暂停 p 恢复]", style="bold yellow")
    if store.facts is None:
        t.append("   [待机]", style="cyan")
    return t


HINT = "q 退出  m 换模型  s 停止  r 重启  l 日志  p 暂停  c 清历史  h 温度"
