"""Process discovery: everything about the running server comes from the live
process command line (zero-modification contract; port/api-key/model follow
whatever the user's recipe actually launched)."""

from __future__ import annotations

import psutil

from ..model import ServerFacts

PROC_NAME = "llama-server.exe"

# flags that consume the next token as their value
_VALUE_FLAGS = {
    "-m", "--model", "--port", "--api-key", "-c", "--ctx-size", "--ctx-len",
    "--parallel", "-np", "--alias", "-ma", "--host", "-h", "--spec-type",
    "--model-content", "--lora", "--adapter",
}


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
    """All running llama-server.exe processes (v1 assumes a single active one)."""
    out: list[ServerFacts] = []
    for p in psutil.process_iter(["name", "pid", "exe", "cmdline"]):
        try:
            if (p.info["name"] or "").lower() != PROC_NAME:
                continue
            cmd = p.info["cmdline"] or []
            if not cmd:
                continue
            out.append(parse_cmdline(p.info["pid"], p.info["exe"] or cmd[0], cmd))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    out.sort(key=lambda f: f.pid)
    return out
