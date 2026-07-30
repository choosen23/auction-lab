"""Stdlib HTTP server: static files from ``web/``, ``GET /mechanisms``, ``POST /run``.

The engine knows nothing about HTTP; this module knows nothing about auctions beyond
the shape of a request. ``POST /run`` is the trust boundary, so :func:`validate` runs
before anything reaches the engine, and it is a plain function so the tests can call it
without a socket.

ponytail: stdlib ``http.server``, no FastAPI — two endpoints do not earn a dependency,
and a dependency would also block the eventual Pyodide deploy. Ceiling: no async, no
middleware, one thread per connection. Upgrade at ~5 endpoints or when async is needed.

Run it with ``python3 -m agt.serve [--port 8000]``.
"""

import argparse
import json
import math
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from agt.mechanisms import REGISTRY, registry_schema, run
from agt.trace import Bidder

# Loopback only. This is a local teaching tool with no authentication of any kind, so
# binding 0.0.0.0 would publish an arbitrary-code-adjacent surface to the whole LAN.
HOST = "127.0.0.1"
DEFAULT_PORT = 8000

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"

# 12 is where the visualization stops being readable; it also bounds compute.
MAX_BIDDERS = 12
# A trace request is a few hundred bytes. 64 KB is generous and still bounds memory.
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


# ------------------------------------------------------------------- validation


def validate(payload: Any) -> dict[str, Any]:
    """Check an untrusted ``/run`` body and return ``{mechanism, bidders, params}``.

    Raises :class:`ValueError` with a message meant for a human staring at the setup
    form. Every branch here exists to make sure a malformed body never reaches the
    engine as a ``KeyError`` or a ``TypeError``.
    """
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")

    name = payload.get("mechanism")
    if not isinstance(name, str) or name not in REGISTRY:
        raise ValueError(
            f"unknown mechanism {name!r}; expected one of {sorted(REGISTRY)}"
        )

    entries = payload.get("bidders")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_BIDDERS:
        raise ValueError(
            f"bidders must be a list of between 1 and {MAX_BIDDERS} entries"
        )
    bidders = [_bidder(i, entry) for i, entry in enumerate(entries)]
    seen = [b.id for b in bidders]
    if len(set(seen)) != len(seen):
        raise ValueError(f"bidder ids must be unique, got {seen}")

    params = payload.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("params must be a JSON object")
    # The mechanism owns its parameter list, so ask the registry rather than restating
    # it here. Ranges and types are checked by the engine on the way in, and its
    # ValueError becomes the same 400 this one would.
    declared = REGISTRY[name].params
    for key in params:
        if key not in declared:
            raise ValueError(
                f"unknown parameter {key!r} for {name!r}; "
                f"expected one of {sorted(declared)}"
            )

    return {"mechanism": name, "bidders": bidders, "params": dict(params)}


def _bidder(index: int, entry: Any) -> Bidder:
    where = f"bidder {index}"
    if not isinstance(entry, dict):
        raise ValueError(f"{where} must be a JSON object with id, value and bid")
    bidder_id = entry.get("id")
    if not isinstance(bidder_id, str) or not bidder_id.strip():
        raise ValueError(f"{where} needs a non-empty string id, got {bidder_id!r}")
    return Bidder(
        id=bidder_id,
        value=_number(f"{where} ({bidder_id}) value", entry.get("value")),
        bid=_number(f"{where} ({bidder_id}) bid", entry.get("bid")),
    )


def _number(where: str, x: Any) -> float:
    # bool is an int subclass, and ``True`` as a bid would silently become 1.
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ValueError(f"{where} must be a number, got {x!r}")
    # json.loads accepts the NaN and Infinity literals, so these do arrive over the
    # wire — and json.dumps would then emit a trace no browser can parse.
    try:
        finite = math.isfinite(x)
    except OverflowError:  # JSON ints are unbounded; 10**400 has no float to test
        finite = False
    if not finite:
        raise ValueError(f"{where} must be finite, got {x!r:.60}")
    if x < 0:
        raise ValueError(f"{where} must be non-negative, got {x!r}")
    return x


# --------------------------------------------------------------------- running


def run_payload(payload: Any) -> tuple[int, dict[str, Any]]:
    """Validate and run one request. Returns ``(status, body)`` and never raises."""
    try:
        valid = validate(payload)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    try:
        trace = run(valid["mechanism"], valid["bidders"], valid["params"])
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except Exception as exc:  # a bug in a mechanism, not in the request
        return 500, {
            "error": f"{type(exc).__name__}: {exc}",
            "partial_trace": _partial_trace(valid, exc),
            # ponytail: the traceback goes to the client. Ceiling — it leaks source
            # paths, which is fine for a loopback-only teaching tool and is the point:
            # a broken mechanism stays debuggable in the UI. Upgrade: gate it behind a
            # --debug flag the day this is ever served to anyone but its author.
            "traceback": traceback.format_exc(),
        }
    return 200, trace.to_dict()


def _partial_trace(valid: dict[str, Any], exc: BaseException) -> dict[str, Any]:
    """Recover the steps a mechanism emitted before it blew up.

    ponytail: read them out of ``run``'s still-live frame in the traceback instead of
    giving the engine an out-parameter it has no other use for. Ceiling — it is coupled
    to the name of one local in :func:`agt.registry.run`, and degrades to an empty step
    list if that changes; ``test_run_payload_returns_500_with_the_partial_trace`` is
    what catches the rename. Upgrade: have ``run`` accept a per-step callback.
    """
    steps: list[Any] = []
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code is run.__code__:
            steps = tb.tb_frame.f_locals.get("steps") or []
            break
        tb = tb.tb_next
    return {
        "mechanism": valid["mechanism"],
        "params": valid["params"],
        "bidders": [b.to_dict() for b in valid["bidders"]],
        "steps": [s.to_dict() for s in steps],
        "result": None,
    }


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


class Handler(BaseHTTPRequestHandler):
    """Four routes. Anything else is a JSON 404, because the client is a fetch() call."""

    server_version = "agt"
    sys_version = ""
    timeout = READ_TIMEOUT

    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        if route == "/mechanisms":
            self._json(200, registry_schema())
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
        if route != "/run":
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
        self._json(*run_payload(payload))

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
