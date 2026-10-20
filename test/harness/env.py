"""Process-wide guards. test/conftest.py calls install() before any yahbah module is imported.

- DATABASE_URL points at a database that doesn't exist, so code that escapes the `db` fixture's
  patch fails loudly instead of touching the real `yahbah` database.
- ARTIFACTS_DIR, PERSONAL_PATH and PROMPTS_PATH point at a per-session temp dir, the synthetic
  applicant (harness.profile) and the public prompts file: tests never write artifacts/ or read
  config/personal*.yaml.
- browser-use telemetry, cloud sync and its PyPI version check are off (the version check runs at
  every Agent start otherwise, which is network).
- DNS lookups of anything but localhost raise socket.gaierror (recorded in BLOCKED_HOSTS), except
  names under FAKE_DOMAIN, which resolve to FAKE_PUBLIC_IP so job URLs pass the intake policy
  (url_utils.validate_job_url) without a real lookup.
- Python sockets refuse to connect anywhere but loopback (recorded in BLOCKED_CONNECTIONS), so a
  fake-resolved or public-IP URL can never be fetched for real.
- OllamaClient and browser-use's ChatOllama raise unless a live_llm test enabled them.
"""
import ipaddress
import os
import socket
import tempfile
from pathlib import Path

from harness import profile

REPO = Path(__file__).resolve().parents[2]
SESSION_DIR = Path(tempfile.mkdtemp(prefix="yahbah-tests-"))
GUARD_DATABASE_URL = "postgresql+psycopg://yahbah_test@localhost/yahbah_test_unpatched_guard"
FAKE_DOMAIN = "harness.example"
FAKE_PUBLIC_IP = "93.184.215.14"  # a global address; the connect guard stops any real connection to it

BLOCKED_HOSTS: list[str] = []
BLOCKED_CONNECTIONS: list[str] = []
_LOCAL_HOSTS = {None, "", "localhost", "127.0.0.1", "::1", "0.0.0.0"}
_ollama = {"allowed": False}
_installed = False


class OllamaBlocked(AssertionError):
    """A test reached a real model. Script it (harness.llm / harness.agent) or mark the test live_llm."""


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    os.environ["DATABASE_URL"] = GUARD_DATABASE_URL
    os.environ["ARTIFACTS_DIR"] = str(SESSION_DIR / "artifacts")
    os.environ["PROMPTS_PATH"] = str(REPO / "config" / "prompts.yaml")
    os.environ["PERSONAL_PATH"] = str(profile.write_personal(SESSION_DIR / "personal.yaml"))
    os.environ["ANONYMIZED_TELEMETRY"] = "false"
    os.environ["BROWSER_USE_CLOUD_SYNC"] = "false"
    os.environ["BROWSER_USE_VERSION_CHECK"] = "false"
    os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "warning")
    _install_network_guard()
    _install_ollama_guard()


def _is_local(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode()
    return host in _LOCAL_HOSTS or host.startswith("127.") or host.endswith(".localhost")


def _is_fake(host) -> bool:
    return isinstance(host, str) and (host == FAKE_DOMAIN or host.endswith("." + FAKE_DOMAIN))


def _loopback_address(address) -> bool:
    if not isinstance(address, tuple):
        return True  # AF_UNIX paths
    try:
        ip = ipaddress.ip_address(address[0].split("%", 1)[0])
    except ValueError:
        return _is_local(address[0])
    return ip.is_loopback or ip.is_unspecified


def _install_network_guard() -> None:
    real_getaddrinfo = socket.getaddrinfo
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def getaddrinfo(host, port, *args, **kwargs):
        if _is_local(host):
            return real_getaddrinfo(host, port, *args, **kwargs)
        if _is_fake(host):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (FAKE_PUBLIC_IP, port or 0))]
        BLOCKED_HOSTS.append(host.decode() if isinstance(host, bytes) else host)
        raise socket.gaierror(socket.EAI_NONAME, f"offline test suite: DNS lookup of {host!r} blocked")

    def check(sock, address) -> None:
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not _loopback_address(address):
            BLOCKED_CONNECTIONS.append(str(address))
            raise OSError(f"offline test suite: connection to {address!r} blocked")

    def connect(self, address):
        check(self, address)
        return real_connect(self, address)

    def connect_ex(self, address):
        check(self, address)
        return real_connect_ex(self, address)

    socket.getaddrinfo = getaddrinfo
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex


def _install_ollama_guard() -> None:
    from browser_use.llm.ollama.chat import ChatOllama

    from yahbah.llm.client import OllamaClient

    real_call, real_ainvoke = OllamaClient._call_ollama, ChatOllama.ainvoke

    async def call_ollama(self, *args, **kwargs):
        if not _ollama["allowed"]:
            raise OllamaBlocked(f"OllamaClient({self._role!r}) called outside a live_llm test")
        return await real_call(self, *args, **kwargs)

    async def ainvoke(self, *args, **kwargs):
        if not _ollama["allowed"]:
            raise OllamaBlocked(f"ChatOllama({self.model!r}) called outside a live_llm test")
        return await real_ainvoke(self, *args, **kwargs)

    OllamaClient._call_ollama = call_ollama
    ChatOllama.ainvoke = ainvoke


def allow_ollama(allowed: bool) -> None:
    _ollama["allowed"] = allowed
