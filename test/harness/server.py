"""Local HTTP fixture server: stdlib http.server on an ephemeral 127.0.0.1 port, in a daemon thread.

Serves test/yahbah/fixtures/ at / and any directory mounted under its own prefix (per-test pages).
browser-use only indexes pages it navigated to over http(s) (about:blank has an empty selector
map), so every page that needs clicks or agent actions is loaded from here.

Pages report what happened with `fetch('/__event?name=submit&...')`; the server records each
request in `events`, which survives page navigation (unlike a window counter).
"""
import asyncio
import functools
import http.server
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

FIXTURES = Path(__file__).resolve().parents[1] / "yahbah" / "fixtures"


@dataclass
class Event:
    name: str
    params: dict[str, str]
    method: str
    at: float = field(default_factory=time.time)


class FixtureServer:
    def __init__(self, root: Path = FIXTURES) -> None:
        self.mounts: dict[str, Path] = {"/": root}
        self.events: list[Event] = []
        self._lock = threading.Lock()
        handler = functools.partial(_Handler, self)
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="fixture-server", daemon=True)

    def start(self) -> "FixtureServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def url(self, path: str = "") -> str:
        return f"{self.base}/{path.lstrip('/')}"

    def mount(self, prefix: str, directory: Path) -> str:
        """Serve `directory` under /<prefix>/; returns that base URL (with a trailing slash)."""
        prefix = "/" + prefix.strip("/") + "/"
        self.mounts[prefix] = Path(directory)
        return f"{self.base}{prefix}"

    def unmount(self, prefix: str) -> None:
        self.mounts.pop("/" + prefix.strip("/") + "/", None)

    def record(self, event: Event) -> None:
        with self._lock:
            self.events.append(event)

    def events_named(self, name: str) -> list[Event]:
        with self._lock:
            return [e for e in self.events if e.name == name]

    async def wait_events(self, name: str, count: int, timeout: float = 2.0) -> int:
        """How many `name` events have arrived, waiting up to `timeout` for `count` of them (beacons
        are sent asynchronously by the page). Requests are handled on concurrent threads, so events
        of different names are not necessarily recorded in the order the page sent them."""
        deadline = time.monotonic() + timeout
        while (n := len(self.events_named(name))) < count and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        return n

    def clear_events(self) -> None:
        with self._lock:
            self.events.clear()

    def resolve(self, url_path: str) -> Path | None:
        prefix = max((p for p in self.mounts if url_path.startswith(p) or url_path + "/" == p), key=len, default=None)
        if prefix is None:
            return None
        root = self.mounts[prefix].resolve()
        target = (root / url_path[len(prefix):].lstrip("/")).resolve()
        if target != root and root not in target.parents:
            return None  # no ../ escapes
        return target


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, server_state: FixtureServer, *args, **kwargs) -> None:
        self.state = server_state
        super().__init__(*args, directory=str(server_state.mounts["/"]), **kwargs)

    def log_message(self, *args) -> None:
        pass

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def translate_path(self, path: str) -> str:
        target = self.state.resolve(urlsplit(path).path)
        return str(target) if target else "/nonexistent"

    def _event(self) -> bool:
        parts = urlsplit(self.path)
        if parts.path != "/__event":
            return False
        params = {k: v[-1] for k, v in parse_qs(parts.query).items()}
        self.state.record(Event(name=params.pop("name", ""), params=params, method=self.command))
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(204)
        self.end_headers()
        return True

    def do_GET(self) -> None:
        if not self._event():
            super().do_GET()

    def do_POST(self) -> None:
        if not self._event():
            self.send_error(405)
