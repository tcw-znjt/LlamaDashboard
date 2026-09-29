"""Pure renderable builders (Rich) shared by the TUI - no Textual imports here,
so layout content can be unit-tested off-screen."""

from __future__ import annotations

import time

from rich.console import Console, Group
from rich.table import Table
from rich.text import Text, cell_len

from .health import HealthBoard
from .model import SlotLive
from .sources.proc import proc_label, short_names
from .store import SessionStore

BAR_W = 22
# 一个带 round 边框 + `padding: 0 1` 的面板在内容宽度之外占的额外列数
PANEL_CHROME_W = 4


def content_width(renderable) -> int:
    """渲染体的自然宽度(按终端列计,CJK 记 2 列)。"""
    c = Console(width=200, force_terminal=False)
    with c.capture() as cap:
        c.print(renderable)
    return max((cell_len(ln) for ln in cap.get().splitlines()), default=0)


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


def _avg_label(server_val: float | None, session_val: float | None) -> str:
    """运行均值:优先服务端自报值,取不到时降级为会话均值并标注来源。"""
    if server_val is not None:
        return f"运行均值 {_num(server_val, '', 1)}"
    return f"运行均值 {_num(session_val, '', 1)} (会话)"


def header_text(store: SessionStore) -> Text:
    t = Text()
    f, d = store.facts, store.dialect
    if f is None:
        t.append(" llama.cpp 监控 ", style="bold cyan")
        t.append(f" 待机:未发现 server 进程({' / '.join(short_names())},按 m 从 profile 启动)")
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
    t.append("  进程 ", style="dim"); t.append(proc_label(f.exe))
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
    in_tps = store.prompt_rate()          # 实时/冻结值,不回落到上一请求
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
    mtp_on = (bool(store.facts and store.facts.mtp)
              or (cur is not None and cur.speculative)
              or bool(store.mtp_enabled))

    cells: list[tuple[str, Text, Text]] = []
    v = Text(_num(out_tps, " t/s"), style="bold green") if out_tps else Text("—")
    cells.append(("输出速度", v, Text(_avg_label(store.server_avg_out_tps, tot["avg_out_tps"]),
                                     style="dim")))
    v = Text(_num(in_tps, " t/s"), style="bold cyan") if in_tps else Text("—")
    cells.append(("输入速度", v, Text(_avg_label(store.server_avg_in_tps, tot["avg_in_tps"]),
                                     style="dim")))
    cells.append(("KV 命中率", Text(_pct(kv_cum), style="bold magenta"),
                  Text(f"本次 {_pct(kv_cur)}", style="dim")))
    # MTP:有数据显示百分比;启用但日志没给统计(如 verbosity 不够)时明确区分
    if mtp_val is None:
        mtp_sub = "启用·无统计" if mtp_on else "未启用"
    else:
        mtp_sub = "本次监控"
    cells.append(("MTP 接受率",
                  Text(_pct(mtp_val), style="bold yellow") if mtp_val is not None else Text("—"),
                  Text(mtp_sub, style="dim")))
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
    row("频率", Text(f"核心 {g.clk_core if g.clk_core is not None else '—'} MHz"),
        Text(f"显存 {g.clk_mem if g.clk_mem is not None else '—'} MHz"))
    extra = store.gpu_extra
    t1 = Text(f"GPU {g.temp_c if g.temp_c is not None else '—'} C")
    t2 = Text(f"风扇 {g.fan_pct if g.fan_pct is not None else '—'}%" if g.fan_pct is not None else "风扇 —")
    t2.append(f"   显存 {extra['显存']:.0f} C" if "显存" in extra else "   显存 —")
    t2.append(f"   热点 {extra['热点']:.0f} C" if "热点" in extra else "   热点 —")
    row("温度", t1, t2)
    if detail:
        zones = "    ".join(f"{k} {v:.0f} C" for k, v in extra.items() if k.startswith("区"))
        tb.add_row(Text("分区", style="dim"), Text(zones or "驱动不提供(—)", style="dim"), Text(""))
    return Group(name, tb)


def _ctx_total(store: SessionStore, cur: SlotLive | None) -> int | None:
    """上下文总长:当前槽 → 命令行 -c → 任一槽快照。"""
    if cur is not None and cur.n_ctx:
        return cur.n_ctx
    if store.facts is not None and store.facts.n_ctx:
        return store.facts.n_ctx
    for s in store.slots.values():
        if s.n_ctx:
            return s.n_ctx
    return None


def _peak_text(peak: int | None, ctx: int | None) -> str:
    """上下文峰值:没有总量时只给已用值,都没有时占位。"""
    if peak is None:
        return "上下文峰值 —"
    if not ctx:
        return f"上下文峰值 {peak / 1000:.1f}k"
    return f"上下文峰值 {peak / 1000:.1f}k / {ctx / 1000:.1f}k ({peak / ctx * 100:.0f}%)"


def live_prompt_text(store: SessionStore) -> Table:
    """实时 prompt 面板:阶段、处理进度、输入速度、本次输入与缓存命中。

    四个字段常显(默认就是最全的字段集):当前取不到实时值时回落到最近一次
    有效值(冻结的 prompt 速率 / 上一请求的输入与缓存),从未有值时显示 `—`,
    面板不会因阶段切换而少行。
    """
    cur = store.current_slot()
    last = store.last_request()
    tb = Table.grid(padding=(0, 1))
    tb.add_row(Text("prompt", style="bold cyan"))
    if store.facts is None:                       # 待机:字段仍常显,值全是占位
        tb.add_row(Text("阶段 待机", style="dim"))
        tb.add_row(Text("处理输入 —", style="dim"))
        tb.add_row(Text("输入速度 ") + Text("—", style="bold cyan"))
        tb.add_row(Text("本次输入 — tok (缓存命中 —)", style="dim"))
        return tb
    rate = store.prompt_rate()                    # prompt 中=实时值,之后=冻结值
    if cur is None:                               # 空闲:没有进行中的进度,回落到上次值
        speed = rate if rate is not None else (last.in_tps if last else None)
        tb.add_row(Text("阶段 空闲", style="bold green"))
        tb.add_row(Text("处理输入 —", style="dim"))
        tb.add_row(Text("输入速度 ") + Text(_num(speed, " t/s") if speed else "—",
                                           style="bold cyan"))
        shown = last.input_tokens if last else None
        cache = last.kv_cache_tokens if last else None
        tb.add_row(Text(f"上次输入 {shown if shown is not None else '—'} tok "
                        f"(缓存命中 {cache if cache is not None else '—'})", style="dim"))
        return tb
    phase = "处理中" if cur.phase == "prompt" else "已完成"
    tb.add_row(Text(f"阶段 {phase}"))
    if cur.phase == "prompt":
        prog = store.prompt_progress_for(cur.id)
        if prog is not None:                       # (a) log 实时进度
            tb.add_row(Text(f"处理输入 {prog[0] * 100:.0f}%"))
        else:                                      # (b) 该版本无实时行,回落到已用时间
            el = store.prompt_elapsed_s(cur.id)
            tb.add_row(Text(f"处理输入 {el:.0f}s" if el is not None else "处理输入 —"))
    else:                                          # (c) 结算后
        tb.add_row(Text(f"处理输入 {cur.n_prompt_tokens_processed}/{cur.eval_total} tok"))
    tb.add_row(Text("输入速度 ") + Text(_num(rate, " t/s") if rate else "—", style="bold cyan"))
    tb.add_row(Text(f"本次输入 {cur.fresh_prompt or cur.n_prompt_tokens} tok "
                    f"(缓存命中 {cur.n_prompt_tokens_cache})"))
    return tb


def live_decode_text(store: SessionStore) -> Table:
    """实时 decode 面板:阶段、已生成/剩余、输出速度、上下文峰值(同 prompt 面板的常显规则)。"""
    cur = store.current_slot()
    last = store.last_request()
    tb = Table.grid(padding=(0, 1))
    tb.add_row(Text("decode", style="bold green"))
    if store.facts is None:
        tb.add_row(Text("阶段 待机", style="dim"))
        tb.add_row(Text("已生成 — tok   剩余 —", style="dim"))
        tb.add_row(Text("输出速度 ") + Text("—", style="bold green"))
        tb.add_row(Text(_peak_text(None, None), style="dim"))
        return tb
    if cur is None:
        gen = last.output_tokens if last else None
        speed = last.out_tps if last else None
        peak = (last.input_tokens + last.output_tokens) if last else None
        tb.add_row(Text("阶段 空闲", style="bold green"))
        tb.add_row(Text(f"已生成 {gen if gen is not None else '—'} tok   剩余 —", style="dim"))
        tb.add_row(Text("输出速度 ") + Text(_num(speed, " t/s") if speed else "—",
                                           style="bold green"))
        tb.add_row(Text(_peak_text(peak, _ctx_total(store, cur)), style="dim"))
        return tb
    tb.add_row(Text("解码中" if cur.phase == "decode" else "等待生成"))
    tb.add_row(Text(f"已生成 {cur.n_decoded} tok   剩余 "
                    f"{('不限' if cur.n_remain < 0 else cur.n_remain)}"))
    rate = cur.rate_out if cur.rate_out is not None else store.live_out_tps
    tb.add_row(Text("输出速度 ") + Text(_num(rate, " t/s"), style="bold green"))
    peak = cur.n_prompt_tokens              # already includes appended generation
    tb.add_row(Text(_peak_text(peak, _ctx_total(store, cur))))
    return tb


def panel_width(renderable) -> int:
    """带框面板需要的最小宽度:文字内容宽度 + 边框与内边距。"""
    return content_width(renderable) + PANEL_CHROME_W


def history_panel_width(store: SessionStore, top_widths: list[int], stacked: bool,
                        available: int, max_rows: int = 10) -> int:
    """请求历史面板宽度:先满足内容最小宽度,再为对齐适当变宽。

    - 三框同行:目标 = 三框宽度之和(与上方行等宽);
    - 三框堆叠:目标 = 四个面板中最宽者(与上方框同宽);
    - 内容本身更宽时以内容为准;最终不超过终端可用宽度。
    """
    need = panel_width(history_text(store, max_rows=max_rows))
    target = max(top_widths + [need]) if stacked else max(sum(top_widths), need)
    return min(target, available) if available > 0 else target


def row_fits(panels, available: int) -> bool:
    """三框(各带边框与内边距)能否在同一行里完整排下。"""
    return sum(content_width(p) + PANEL_CHROME_W for p in panels) <= available


def history_text(store: SessionStore, max_rows: int = 10) -> Table:
    """请求历史表:列宽由表头与可见行内容自适应(数字右对齐),列间只留必要 padding。

    不写死每列宽度:固定宽度会为最坏情况预留空间,数值位数少时列内剩余宽度
    全变成左侧空白(实测「槽」与「输入」之间会出现 8 列空档)。
    """
    tb = Table(box=None, pad_edge=False, expand=False)
    tb.title = None
    for col in ("时间", "槽", "输入", "输出", "输入t/s", "输出t/s", "KV", "MTP", "耗时ms"):
        tb.add_column(col, style="dim", justify="right")
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
