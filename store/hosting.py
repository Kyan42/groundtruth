"""Hosted mode: the settings both processes (the verifier service and the results store) read at start-up.

    GT_HOSTED=1        hosted mode: every request needs a user (a bearer token, or a page's sign-in cookie on the store);
                       nothing is allowed because it looks local; a process may bind to 0.0.0.0
    GT_HOST            the address to bind (127.0.0.1 when not given; --host overrides it); ports are always --port
    GT_DATA            the data folder holding store.db, shared by both processes (--data overrides it; <repo>/.store)
    GT_PUBLIC_URL      hosted, the store: the address people use, such as https://gt.example.com; a page's write with
                       a sign-in cookie must come from this origin (without it, the request's own Host is the origin)
    GT_SERVICE_URL     hosted, the store: where the verifier service listens on this machine, for /verifier/ forwarding
                       (http://127.0.0.1:8790 when not given)

Read only by serve() and the command line; make_server() takes plain arguments, so tests run both modes side by side.
"""
import ipaddress
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")


def hosted_from_env() -> bool:
    return os.environ.get("GT_HOSTED", "").strip().lower() in ("1", "true", "yes")


def is_loopback(addr) -> bool:
    try:
        ip = ipaddress.ip_address(str(addr).split("%")[0])
    except ValueError:
        return False
    return ip.is_loopback or bool(getattr(ip, "ipv4_mapped", None) and ip.ipv4_mapped.is_loopback)


def bind_host(given, hosted: bool) -> str:
    """--host, else GT_HOST, else 127.0.0.1. Only hosted mode may listen beyond this machine."""
    host = (given or os.environ.get("GT_HOST") or "127.0.0.1").strip()
    if host not in LOOPBACK_NAMES and not hosted:
        raise SystemExit(f"Listening on {host} lets other machines connect, which only hosted mode allows (GT_HOSTED=1). "
                         "Without it, use 127.0.0.1.")
    if host not in LOOPBACK_NAMES + ("0.0.0.0",):
        raise SystemExit(f"Give 127.0.0.1 or 0.0.0.0 as the host, not {host}: the verifier's own helper reaches it on 127.0.0.1.")
    return host


def data_dir(given=None) -> Path:
    """--data, else GT_DATA, else <repo>/.store."""
    return Path(given or os.environ.get("GT_DATA") or ROOT / ".store").resolve()
