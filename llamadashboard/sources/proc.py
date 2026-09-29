"""Process discovery: everything about the running server comes from the live
process command line (zero-modification contract; port/api-key/model follow
whatever the user's recipe actually launched).

Several llama.cpp builds ship different exe names (plain `llama-server.exe` and
the KV-memory variant `llama-kvmem-server.exe`); any of them is monitored the
same way - whichever is running is the one we attach to."""

from __future__ import annotations

import psutil

from ..model import ServerFacts

PROC_NAMES = ("llama-server.exe", "llama-kvmem-server.exe")
PROC_NAME = PROC_NAMES[0]        # backwards-compatible alias

# flags that consume the next token as their value
_VALUE_FLAGS = {
    "-m", "--model", "--port", "--api-key", "-c", "--ctx-size", "--ctx-len",
    "--parallel", "-np", "--alias", "-ma", "--host", "-h", "--spec-type",
    "--model-content", "--lora", "--adapter",
}


def is_server_proc(name: str | None) -> bool:
    """Case-insensitive match against the known server exe names."""
    return (name or "").lower() in PROC_NAMES


def short_names() -> tuple[str, ...]:
    """Known exe names without the suffix, for hints (llama-server / llama-kvmem-server)."""
    return tuple(n[:-4] if n.lower().endswith(".exe") else n for n in PROC_NAMES)


def proc_label(exe: str | None) -> str:
    """Short build label for the header, e.g. 'llama-kvmem-server'."""
    if not exe:
        return "—"
    name = exe.replace("\\", "/").rsplit("/", 1)[-1]
    return name[:-4] if name.lower().endswith(".exe") else (name or exe)


def parse_cmdline(pid: int, exe: str, cmdline: list[str]) -> ServerFacts:
    """Extract value flags from an argv list; supports `--flag value` and `--flag=value`."""
    args: dict[str, str] = {}
    i = 0
    while i < len(cmdline):
        tok = cmdline[i]
        if tok.startswith("-"):
            if "=" in tok:
                key, _, val = tok.partition("=")
                args.setdefault(key, val)
            elif tok in _VALUE_FLAGS and i + 1 < len(cmdline):
                args.setdefault(tok, cmdline[i + 1])
                i += 1
        i += 1
    return ServerFacts(pid=pid, exe=exe, cmdline=list(cmdline), args=args)


def discover() -> list[ServerFacts]:
    """All running server processes, any known exe name (v1 monitors one at a
    time: caller takes the first, i.e. the lowest pid)."""
    out: list[ServerFacts] = []
    for p in psutil.process_iter(["name", "pid", "exe", "cmdline"]):
        try:
            if not is_server_proc(p.info["name"]):
                continue
            cmd = p.info["cmdline"] or []
            if not cmd:
                continue
            out.append(parse_cmdline(p.info["pid"], p.info["exe"] or cmd[0], cmd))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    out.sort(key=lambda f: f.pid)
    return out
