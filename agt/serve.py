"""Stdlib HTTP server: static files from ``web/``, three schema routes, three run routes.

This module is transport and nothing else — routing, headers, path safety, byte caps.
What a request *means* lives in :mod:`agt.api`, whose functions take JSON and return
``(status, body)`` with no socket in sight, so the tests can call them directly. The
public names are re-exported here because ``agt.serve`` is the front door.

ponytail: stdlib ``http.server``, no FastAPI — five endpoints do not earn a dependency,
and a dependency would also block the eventual Pyodide deploy. Ceiling: no async, no
middleware, one thread per connection. Upgrade at ~10 endpoints or when async is needed.

Run it with ``python3 -m agt.serve [--port 8000]``.
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from agt.api import (
    DEFAULT_ROUNDS,
    MAX_BIDDERS,
    equilibrium_payload,
    run_payload,
    run_series_payload,
    validate,
    validate_equilibrium,
    validate_series,
)
from agt.mechanisms import registry_schema
from agt.presets import preset_schema
from agt.strategies import strategy_schema

__all__ = [
    "DEFAULT_ROUNDS",
    "MAX_BIDDERS",
    "equilibrium_payload",
    "preset_schema",
    "run_payload",
    "run_series_payload",
    "serve",
    "validate",
    "validate_equilibrium",
    "validate_series",
]

# Loopback only. This is a local teaching tool with no authentication of any kind, so
# binding 0.0.0.0 would publish an arbitrary-code-adjacent surface to the whole LAN.
HOST = "127.0.0.1"
DEFAULT_PORT = 8000

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"

# A run request is a few hundred bytes; a series request adds one strategy per bidder.
# 64 KB is generous for both and still bounds memory. It is not what bounds *compute* —
# the bidder and round caps in agt.api do that, because a 300-byte body can ask for the
# most expensive series there is.
MAX_BODY = 64 * 1024
# Read timeout per connection, so a client that promises bytes it never sends cannot
# pin a thread forever.
READ_TIMEOUT = 10

# An allow-list, not ``mimetypes.guess_type``: an unexpected file in web/ is served as
# an opaque download rather than as something the browser will parse and execute.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}
DEFAULT_CONTENT_TYPE = "application/octet-stream"
JSON_CONTENT_TYPE = "application/json"


# --------------------------------------------------------------- static files


def resolve_static(url_path: str, root: Path | None = None) -> Path | None:
    """Map a URL path to a file inside ``root``, or ``None`` if it escapes or is absent.

    The check is "resolve, then ask whether the result is still inside the root", which
    covers ``..``, percent-encoded ``..``, absolute paths and symlinks out of the tree
    in one step. String-matching on ``..`` would not.
    """
    root = (root or WEB_ROOT).resolve()
    relative = unquote(urlsplit(url_path).path).lstrip("/") or "index.html"
    try:
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return None
    except (OSError, ValueError):  # NUL bytes, overlong names, unreadable parents
        return None
    return target


def content_type(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), DEFAULT_CONTENT_TYPE)


# ---------------------------------------------------------------- the handler

# The routing tables, so a new endpoint is a line rather than another branch. Both are
# read-only lookups; anything not in them falls through to static files or a JSON 404.
SCHEMAS = {
    "/mechanisms": registry_schema,
    "/strategies": strategy_schema,
    "/presets": preset_schema,
}
RUNNERS = {
    "/run": run_payload,
    "/run_series": run_series_payload,
    "/equilibrium": equilibrium_payload,
}


class Handler(BaseHTTPRequestHandler):
    """Six endpoints and the static tree. Anything else is a JSON 404: the client is fetch()."""

    server_version = "agt"
    sys_version = ""
    timeout = READ_TIMEOUT

    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        schema = SCHEMAS.get(route)
        if schema is not None:
            self._json(200, schema())
            return
        target = resolve_static(self.path)
        try:
            body = target.read_bytes() if target else None
        except OSError:  # unreadable, or deleted between the check and the read
            body = None
        if body is None:
            self._json(404, {"error": f"not found: {route}"})
            return
        self._send(200, body, content_type(target))

    def do_POST(self) -> None:
        route = urlsplit(self.path).path
        runner = RUNNERS.get(route)
        if runner is None:
            self._json(404, {"error": f"not found: {route}"})
            return
        raw = self._read_body()
        if raw is None:
            return  # _read_body has already answered
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._json(400, {"error": f"request body is not valid JSON: {exc}"})
            return
        self._json(*runner(payload))

    def _read_body(self) -> bytes | None:
        """Read at most ``MAX_BODY`` bytes, or answer with an error and return None."""
        declared = self.headers.get("Content-Length")
        if declared is None:
            self._json(411, {"error": "Content-Length header is required"})
            return None
        try:
            length = int(declared)
        except ValueError:
            self._json(400, {"error": f"invalid Content-Length: {declared!r}"})
            return None
        if not 0 <= length <= MAX_BODY:
            self._json(413, {"error": f"request body must be at most {MAX_BODY} bytes"})
            return None
        return self.rfile.read(length)

    def _json(self, status: int, body: dict[str, Any]) -> None:
        self._send(status, json.dumps(body).encode(), JSON_CONTENT_TYPE)

    def _send(self, status: int, payload: bytes, mime: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


# --------------------------------------------------------------- entry point


def serve(port: int = DEFAULT_PORT) -> None:
    httpd = ThreadingHTTPServer((HOST, port), Handler)
    # flush: stdout is block-buffered when piped, and the URL is the whole point.
    print(
        f"agt visualizer: http://{HOST}:{httpd.server_address[1]}/  (ctrl-c to stop)",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python3 -m agt.serve")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve(parser.parse_args(argv).port)


if __name__ == "__main__":
    main()
