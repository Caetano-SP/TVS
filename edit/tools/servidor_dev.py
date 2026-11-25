#!/usr/bin/env python3
"""
Servidor Local de Desenvolvimento e Produção TARGETS_VIRTU
- Pool de threads controlado (ThreadPoolExecutor) sobre ThreadingHTTPServer
- HTTP/1.1 Keep-Alive + TCP_NODELAY + timeout de socket mobile (12s) + sem reverse DNS
- Certificado TLS dinâmico com Subject Alternative Name (SAN) + Session Tickets + OP_NO_COMPRESSION
- Cache LRU em RAM com entrega zero-copy via memoryview e suporte a Brotli (.br) / Gzip (.gz)
- Persistência assíncrona de sessões (/api/sessions) com fila com backpressure e SQLite em modo WAL
"""

import atexit
import email.utils
import gzip
import hashlib
import json
import mimetypes
import multiprocessing
import os
import queue
try:
    import resource
except ImportError:  # pragma: no cover - Windows fallback
    resource = None  # type: ignore[assignment]
import shutil
import signal
import socket
import sqlite3
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import weakref
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, NamedTuple, Optional, Tuple
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent.parent
EDIT_SRC = (BASE_DIR / "edit" / "src").resolve()
DATA_DIR = (BASE_DIR / "data").resolve()
LIB_DIR = (BASE_DIR / "lib").resolve()
MANTIS_IMAGES_DIR = (LIB_DIR / "Mantis-Target-Analysis-main" / "images").resolve()
CERT_DIR = (BASE_DIR / ".cert").resolve()
SESSIONS_DB_PATH = (CERT_DIR / "sessions.db").resolve()

MAX_SESSION_PAYLOAD_BYTES = 65536
SESSION_QUEUE_MAXSIZE = 16384
SESSION_BATCH_SIZE = 512
MAX_STORED_SESSIONS = 10000
SESSION_PRUNE_EVERY_N_COMMITS = 16
DEFAULT_DB_SHARDS = 1
SQLITE_MMAP_SIZE_BYTES = 268435456
SOCKET_TIMEOUT_SECONDS = 12.0
SOCKET_BACKLOG_SIZE = 4096
DEFAULT_MIN_WORKERS = 64
DEFAULT_MAX_WORKERS = 2048
DEFAULT_CLUSTER_MAX_WORKERS = 1024
DEFAULT_WORKER_IDLE_TIMEOUT = 30.0
KEEPALIVE_ADAPTIVE_THRESHOLD = 0.50
KEEPALIVE_PRESSURE_TIMEOUT_SECONDS = 1.5
KEEPALIVE_PRESSURE_RATIO = 0.75
KEEPALIVE_HEADER_TIMEOUT = 5
KEEPALIVE_MAX_REQUESTS = 100
HEALTH_CACHE_TTL_SECONDS = 0.05

# Garante MIME types corretos para assets modernos e Web Workers
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")


def obter_ip_local() -> str:
    """Detecta o IP do computador na rede local (Wi-Fi/Ethernet)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return str(s.getsockname()[0])
    except Exception:
        return "127.0.0.1"


def gerar_certificado_autoassinado(ip_local: Optional[str] = None) -> Tuple[Path, Path]:
    """
    Gera par cert.pem e key.pem com SAN (IP atual, 127.0.0.1 e localhost).
    Regenera automaticamente caso o IP local tenha mudado.
    """
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    cert_path = CERT_DIR / "cert.pem"
    key_path = CERT_DIR / "key.pem"
    ip_track_path = CERT_DIR / "ip.txt"

    ip_local = ip_local or obter_ip_local()
    saved_ip = ip_track_path.read_text(encoding="utf-8").strip() if ip_track_path.exists() else None

    if cert_path.exists() and key_path.exists() and saved_ip == ip_local:
        return cert_path, key_path

    print(f"[*] Gerando certificado SSL autoassinado com SAN para {ip_local}...")
    ext_cfg = (
        "[req]\n"
        "distinguished_name = dn\n"
        "x509_extensions = v3_req\n"
        "prompt = no\n"
        "[dn]\n"
        f"CN = {ip_local}\n"
        "[v3_req]\n"
        "basicConstraints = CA:FALSE\n"
        "keyUsage = digitalSignature, keyEncipherment\n"
        "extendedKeyUsage = serverAuth\n"
        f"subjectAltName = IP:{ip_local},IP:127.0.0.1,DNS:localhost\n"
    )

    with tempfile.NamedTemporaryFile("w", suffix=".cnf", delete=False, encoding="utf-8") as tmp:
        tmp.write(ext_cfg)
        tmp_cfg_path = tmp.name

    try:
        cmd = [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key_path),
            "-out", str(cert_path),
            "-days", "365",
            "-nodes",
            "-config", tmp_cfg_path,
            "-extensions", "v3_req",
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ip_track_path.write_text(ip_local, encoding="utf-8")
        print(f"[+] Certificado SAN gerado em: {CERT_DIR}")
    finally:
        if os.path.exists(tmp_cfg_path):
            os.remove(tmp_cfg_path)

    return cert_path, key_path


def create_ssl_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    """Cria SSLContext otimizado para Wi-Fi móvel (OP_NO_COMPRESSION e Session Tickets)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.options |= getattr(ssl, "OP_NO_COMPRESSION", 0)
    if hasattr(ssl, "OP_NO_TICKET"):
        ctx.options &= ~ssl.OP_NO_TICKET
    if hasattr(ctx, "num_tickets") and ctx.num_tickets < 2:
        ctx.num_tickets = 2
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return ctx


_SENTINEL = object()
_ACTIVE_STORES_LOCK = threading.Lock()
_ACTIVE_STORES: "weakref.WeakSet[SessionStore]" = weakref.WeakSet()
_DEFAULT_STORE_LOCK = threading.Lock()
_DEFAULT_SESSION_STORE: Optional["SessionStore"] = None

DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"
_DEFAULT_L1_CACHE_MAX_BYTES = 32 * 1024 * 1024
_DEFAULT_L1_CACHE_MAX_ENTRIES = 2048
_SESSIONS_VERSION_KEY = "sessions:version"
_SESSIONS_CACHE_TTL_SECONDS = 300.0


class _RedisError(Exception):
    """Erro retornado pelo protocolo RESP2 do Redis."""


class _MinimalRedisClient:
    """Cliente Redis RESP2 leve em stdlib com pool de sockets TCP_NODELAY e timeouts curtos."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        username: Optional[str] = None,
        timeout: float = 0.15,
        max_pool_size: int = 32,
    ) -> None:
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self.username = username
        self.timeout = timeout
        self._pool: "queue.LifoQueue[Tuple[socket.socket, Any]]" = queue.LifoQueue(maxsize=max_pool_size)

    @classmethod
    def from_url(cls, url: str, timeout: float = 0.15) -> "_MinimalRedisClient":
        raw = url.strip()
        if "://" not in raw:
            raw = f"redis://{raw}"
        parsed = urlparse(raw)
        if parsed.scheme not in ("redis", ""):
            raise ValueError(f"Esquema Redis não suportado: {parsed.scheme}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 6379
        db = 0
        path_db = (parsed.path or "").strip("/")
        if path_db:
            db = int(path_db)
        return cls(
            host=host,
            port=port,
            db=db,
            password=parsed.password,
            username=parsed.username,
            timeout=timeout,
        )

    @staticmethod
    def _encode_command(*args: Any) -> bytes:
        parts = [f"*{len(args)}\r\n".encode("ascii")]
        for arg in args:
            if isinstance(arg, bytes):
                b = arg
            elif isinstance(arg, str):
                b = arg.encode("utf-8")
            else:
                b = str(arg).encode("ascii")
            parts.append(f"${len(b)}\r\n".encode("ascii"))
            parts.append(b)
            parts.append(b"\r\n")
        return b"".join(parts)

    @staticmethod
    def _read_exact(rfile: Any, n: int) -> bytes:
        data = rfile.read(n)
        if data is None or len(data) != n:
            raise ConnectionError("Conexão Redis encerrada inesperadamente")
        return data

    @classmethod
    def _read_reply(cls, rfile: Any) -> Any:
        line = rfile.readline()
        if not line or not line.endswith(b"\r\n"):
            raise ConnectionError("Resposta RESP2 incompleta do Redis")
        prefix = line[:1]
        payload = line[1:-2]
        if prefix == b"+":
            return payload.decode("utf-8", errors="replace")
        if prefix == b"-":
            raise _RedisError(payload.decode("utf-8", errors="replace"))
        if prefix == b":":
            return int(payload)
        if prefix == b"$":
            length = int(payload)
            if length == -1:
                return None
            data = cls._read_exact(rfile, length)
            crlf = cls._read_exact(rfile, 2)
            if crlf != b"\r\n":
                raise ConnectionError("Delimitador RESP2 inválido")
            return data
        if prefix == b"*":
            count = int(payload)
            if count == -1:
                return None
            return [cls._read_reply(rfile) for _ in range(count)]
        raise ConnectionError(f"Prefixo RESP2 desconhecido: {prefix!r}")

    @staticmethod
    def _close_conn(conn: Tuple[socket.socket, Any]) -> None:
        sock, rfile = conn
        try:
            rfile.close()
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass

    def _open_conn(self) -> Tuple[socket.socket, Any]:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        rfile = sock.makefile("rb")
        conn = (sock, rfile)
        try:
            if self.password:
                if self.username:
                    sock.sendall(self._encode_command("AUTH", self.username, self.password))
                else:
                    sock.sendall(self._encode_command("AUTH", self.password))
                self._read_reply(rfile)
            if self.db:
                sock.sendall(self._encode_command("SELECT", self.db))
                self._read_reply(rfile)
        except Exception:
            self._close_conn(conn)
            raise
        return conn

    def execute(self, *args: Any) -> Any:
        wire = self._encode_command(*args)
        for attempt in range(2):
            reused = False
            try:
                conn = self._pool.get_nowait()
                reused = True
            except queue.Empty:
                conn = self._open_conn()

            sock, rfile = conn
            try:
                sock.sendall(wire)
                reply = self._read_reply(rfile)
                try:
                    self._pool.put_nowait(conn)
                except queue.Full:
                    self._close_conn(conn)
                return reply
            except (OSError, TimeoutError, ConnectionError):
                self._close_conn(conn)
                if reused and attempt == 0:
                    continue
                raise
            except Exception:
                self._close_conn(conn)
                raise

    def ping(self) -> bool:
        res = self.execute("PING")
        return res in ("PONG", b"PONG")

    def get(self, key: str) -> Optional[bytes]:
        res = self.execute("GET", key)
        if res is None:
            return None
        if isinstance(res, bytes):
            return res
        return str(res).encode("utf-8")

    def set(self, key: str, value: bytes, ttl_seconds: Optional[float] = None) -> bool:
        if ttl_seconds is not None and ttl_seconds > 0:
            px_ms = max(1, int(round(ttl_seconds * 1000.0)))
            res = self.execute("SET", key, value, "PX", px_ms)
        else:
            res = self.execute("SET", key, value)
        return res in ("OK", b"OK", True)

    def delete(self, key: str) -> int:
        return int(self.execute("DEL", key) or 0)

    def incrby(self, key: str, delta: int = 1) -> int:
        return int(self.execute("INCRBY", key, delta))

    def close(self) -> None:
        while True:
            try:
                conn = self._pool.get_nowait()
            except queue.Empty:
                break
            self._close_conn(conn)


class CacheBackend:
    """
    Adaptador de cache híbrido L1 (RAM Zero-Copy) + L2 (Redis com fallback transparente).
    - L1: OrderedDict LRU em RAM local retornando bytes/memoryview em O(1).
    - L2: Integração opcional via REDIS_URL ou 127.0.0.1:6379 para ambientes clusterizados.
    - Fallback transparente em RAM compartilhada/local se o daemon do Redis estiver inativo.
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        l1_max_bytes: int = _DEFAULT_L1_CACHE_MAX_BYTES,
        l1_max_entries: int = _DEFAULT_L1_CACHE_MAX_ENTRIES,
        namespace: str = "targets_virtu:",
        connect_timeout: float = 0.15,
        retry_interval: float = 10.0,
        enable_redis: Optional[bool] = None,
        redis_client: Optional[Any] = None,
    ) -> None:
        self.namespace = namespace
        self.l1_max_bytes = max(1, int(l1_max_bytes))
        self.l1_max_entries = max(1, int(l1_max_entries))
        self.retry_interval = max(0.1, float(retry_interval))

        self._l1_lock = threading.RLock()
        self._l1_cache: "OrderedDict[str, Tuple[bytes, Optional[float]]]" = OrderedDict()
        self._l1_cur_bytes = 0
        self._local_versions: dict[str, int] = {}

        self._stats_lock = threading.Lock()
        self._stats = {
            "l1_hits": 0,
            "l1_misses": 0,
            "l2_hits": 0,
            "l2_misses": 0,
            "l2_fallbacks": 0,
        }

        self._l2_lock = threading.Lock()
        self._l2_retry_at = 0.0
        self._l2_connected = False
        self._l2_client: Optional[Any] = None
        self.redis_url: Optional[str] = None

        if redis_client is not None:
            self._l2_client = redis_client
            self._l2_enabled = True
            self._l2_connected = True
            return

        if enable_redis is False or os.environ.get("TARGETS_REDIS_DISABLED") == "1":
            self._l2_enabled = False
            return

        env_url = os.environ.get("REDIS_URL")
        resolved_url = redis_url if redis_url is not None else env_url
        if resolved_url is None:
            resolved_url = DEFAULT_REDIS_URL

        norm_url = resolved_url.strip()
        if not norm_url or norm_url.lower() in ("none", "off", "disabled", "false"):
            self._l2_enabled = False
            return

        try:
            self._l2_client = _MinimalRedisClient.from_url(norm_url, timeout=connect_timeout)
            self.redis_url = norm_url
            self._l2_enabled = True
        except Exception:
            self._l2_client = None
            self._l2_enabled = False

    def _prefixed(self, key: str) -> str:
        return f"{self.namespace}{key}" if self.namespace else key

    def _inc_stat(self, field: str) -> None:
        with self._stats_lock:
            self._stats[field] += 1

    def stats(self) -> dict[str, Any]:
        with self._stats_lock:
            snap = dict(self._stats)
        with self._l1_lock:
            snap["l1_entries"] = len(self._l1_cache)
            snap["l1_bytes"] = self._l1_cur_bytes
        snap["l2_enabled"] = self._l2_enabled
        snap["l2_active"] = self.is_l2_active()
        snap["l2_connected"] = self._l2_connected
        return snap

    def is_l2_active(self) -> bool:
        if not self._l2_enabled or self._l2_client is None:
            return False
        return time.monotonic() >= self._l2_retry_at

    def reset_l2_backoff(self) -> None:
        with self._l2_lock:
            self._l2_retry_at = 0.0

    def _trip_l2_backoff(self) -> None:
        with self._l2_lock:
            self._l2_retry_at = time.monotonic() + self.retry_interval
            self._l2_connected = False
        self._inc_stat("l2_fallbacks")

    def ping_l2(self) -> bool:
        if not self.is_l2_active():
            return False
        assert self._l2_client is not None
        try:
            ok = bool(self._l2_client.ping())
            self._l2_connected = ok
            return ok
        except Exception:
            self._trip_l2_backoff()
            return False

    def _l1_get(self, key: str) -> Optional[bytes]:
        now = time.monotonic()
        with self._l1_lock:
            item = self._l1_cache.get(key)
            if item is None:
                return None
            value, expires_at = item
            if expires_at is not None and now >= expires_at:
                self._l1_cache.pop(key, None)
                self._l1_cur_bytes -= len(value)
                return None
            self._l1_cache.move_to_end(key)
            return value

    def _l1_set(self, key: str, value: bytes, ttl: Optional[float] = None) -> None:
        val_len = len(value)
        if val_len > self.l1_max_bytes:
            return
        expires_at = (time.monotonic() + ttl) if (ttl is not None and ttl > 0) else None
        with self._l1_lock:
            old = self._l1_cache.pop(key, None)
            if old is not None:
                self._l1_cur_bytes -= len(old[0])

            while (
                (self._l1_cur_bytes + val_len > self.l1_max_bytes or len(self._l1_cache) >= self.l1_max_entries)
                and self._l1_cache
            ):
                _, (evicted_val, _) = self._l1_cache.popitem(last=False)
                self._l1_cur_bytes -= len(evicted_val)

            self._l1_cache[key] = (value, expires_at)
            self._l1_cur_bytes += val_len

    def get(self, key: str, *, bypass_l1: bool = False) -> Optional[bytes]:
        if not bypass_l1:
            local_val = self._l1_get(key)
            if local_val is not None:
                self._inc_stat("l1_hits")
                return local_val
            self._inc_stat("l1_misses")

        if self.is_l2_active():
            assert self._l2_client is not None
            try:
                raw = self._l2_client.get(self._prefixed(key))
                self._l2_connected = True
                if raw is not None:
                    data = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
                    self._inc_stat("l2_hits")
                    self._l1_set(key, data)
                    return data
                self._inc_stat("l2_misses")
            except Exception:
                self._trip_l2_backoff()
        return None

    def get_view(self, key: str, *, bypass_l1: bool = False) -> Optional[memoryview]:
        """Retorna memoryview zero-copy para leituras em L1/L2."""
        data = self.get(key, bypass_l1=bypass_l1)
        return memoryview(data) if data is not None else None

    def set(
        self,
        key: str,
        value: bytes,
        ttl: Optional[float] = None,
        *,
        l1_ttl: Optional[float] = None,
    ) -> None:
        eff_l1_ttl = ttl if l1_ttl is None else l1_ttl
        self._l1_set(key, value, ttl=eff_l1_ttl)

        if self.is_l2_active():
            assert self._l2_client is not None
            try:
                if hasattr(self._l2_client, "set"):
                    try:
                        self._l2_client.set(self._prefixed(key), value, ttl_seconds=ttl)
                    except TypeError:
                        # Compatibilidade com clientes redis-py (ex/px)
                        if ttl is not None and ttl > 0:
                            self._l2_client.set(self._prefixed(key), value, px=max(1, int(round(ttl * 1000.0))))
                        else:
                            self._l2_client.set(self._prefixed(key), value)
                self._l2_connected = True
            except Exception:
                self._trip_l2_backoff()

    def delete(self, key: str) -> None:
        with self._l1_lock:
            old = self._l1_cache.pop(key, None)
            if old is not None:
                self._l1_cur_bytes -= len(old[0])

        if self.is_l2_active():
            assert self._l2_client is not None
            try:
                self._l2_client.delete(self._prefixed(key))
                self._l2_connected = True
            except Exception:
                self._trip_l2_backoff()

    def get_version(self, key: str = _SESSIONS_VERSION_KEY) -> int:
        if self.is_l2_active():
            assert self._l2_client is not None
            try:
                raw = self._l2_client.get(self._prefixed(key))
                self._l2_connected = True
                if raw is None:
                    ver = 0
                else:
                    ver = int(raw.decode("ascii") if isinstance(raw, bytes) else raw)
                with self._l1_lock:
                    self._local_versions[key] = ver
                return ver
            except Exception:
                self._trip_l2_backoff()

        with self._l1_lock:
            return self._local_versions.get(key, 0)

    def incr_version(self, key: str = _SESSIONS_VERSION_KEY, delta: int = 1) -> int:
        with self._l1_lock:
            new_local = self._local_versions.get(key, 0) + delta
            self._local_versions[key] = new_local

        if self.is_l2_active():
            assert self._l2_client is not None
            try:
                if hasattr(self._l2_client, "incrby"):
                    ver = int(self._l2_client.incrby(self._prefixed(key), delta))
                else:
                    ver = int(self._l2_client.incr(self._prefixed(key), delta))
                self._l2_connected = True
                with self._l1_lock:
                    self._local_versions[key] = ver
                return ver
            except Exception:
                self._trip_l2_backoff()

        return new_local

    def get_local_version(self, key: str = _SESSIONS_VERSION_KEY) -> int:
        """Retorna a versão em RAM em O(1) sem chamada de rede (para telemetria /api/health)."""
        with self._l1_lock:
            return self._local_versions.get(key, 0)

    def clear_l1(self) -> None:
        with self._l1_lock:
            self._l1_cache.clear()
            self._l1_cur_bytes = 0
            self._local_versions.clear()

    def close(self) -> None:
        if self._l2_client is not None and hasattr(self._l2_client, "close"):
            try:
                self._l2_client.close()
            except Exception:
                pass


_DEFAULT_CACHE_LOCK = threading.Lock()
_DEFAULT_CACHE_BACKEND: Optional[CacheBackend] = None


def _get_default_cache_backend() -> CacheBackend:
    global _DEFAULT_CACHE_BACKEND
    with _DEFAULT_CACHE_LOCK:
        if _DEFAULT_CACHE_BACKEND is None:
            _DEFAULT_CACHE_BACKEND = CacheBackend()
        return _DEFAULT_CACHE_BACKEND


def _resolve_num_shards(num_shards: Optional[int] = None) -> int:
    if num_shards is not None:
        return max(1, int(num_shards))
    raw = os.environ.get("TARGETS_DB_SHARDS", "").strip()
    if not raw:
        return DEFAULT_DB_SHARDS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_DB_SHARDS


class SessionStore:
    """Persistência assíncrona em SQLite (WAL) com fila 16k, lotes 512, índices compostos e sharding opcional."""

    def __init__(
        self,
        db_path: Path = SESSIONS_DB_PATH,
        maxsize: int = SESSION_QUEUE_MAXSIZE,
        max_stored_sessions: Optional[int] = MAX_STORED_SESSIONS,
        prune_every_commits: int = SESSION_PRUNE_EVERY_N_COMMITS,
        cache_backend: Optional[CacheBackend] = None,
        num_shards: Optional[int] = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.num_shards = _resolve_num_shards(num_shards)
        self.max_stored_sessions = max_stored_sessions
        self.prune_every_commits = max(1, int(prune_every_commits))
        if cache_backend is not None:
            self.cache_backend = cache_backend
        elif self.db_path.resolve() == SESSIONS_DB_PATH:
            self.cache_backend = _get_default_cache_backend()
        else:
            self.cache_backend = CacheBackend(enable_redis=False)

        if self.num_shards == 1:
            self.shard_paths = [self.db_path]
            per_shard_maxsize = maxsize
            self._per_shard_max_stored = max_stored_sessions
        else:
            suffix = self.db_path.suffix or ".db"
            self.shard_paths = [
                self.db_path.parent / f"{self.db_path.stem}_shard_{idx}{suffix}"
                for idx in range(self.num_shards)
            ]
            per_shard_maxsize = max(1, (maxsize + self.num_shards - 1) // self.num_shards)
            if max_stored_sessions is not None and max_stored_sessions > 0:
                self._per_shard_max_stored = max(1, (max_stored_sessions + self.num_shards - 1) // self.num_shards)
            else:
                self._per_shard_max_stored = max_stored_sessions

        self._shard_queues: list["queue.Queue[Any]"] = [
            queue.Queue(maxsize=per_shard_maxsize) for _ in range(self.num_shards)
        ]
        # Mantém compatibilidade direta com testes/chamadores single-shard via _queue e _worker
        self._queue: "queue.Queue[Any]" = self._shard_queues[0]
        self._closed = False
        self._close_lock = threading.Lock()
        self._close_callbacks: list[Any] = []
        self._read_cache_lock = threading.Lock()
        self._read_cache: dict[Tuple[int, Optional[str]], list[dict[str, Any]]] = {}
        self._read_cache_version: Optional[int] = None
        self._json_cache: dict[Tuple[int, Optional[str]], bytes] = {}

        self._workers: list[threading.Thread] = []
        ready_events = [threading.Event() for _ in range(self.num_shards)]
        init_errors: list[Optional[BaseException]] = [None] * self.num_shards

        with _ACTIVE_STORES_LOCK:
            _ACTIVE_STORES.add(self)

        for idx in range(self.num_shards):
            t_name = "session-sqlite-writer" if self.num_shards == 1 else f"session-sqlite-writer-{idx}"
            worker = threading.Thread(
                target=self._worker_loop,
                args=(idx, self.shard_paths[idx], self._shard_queues[idx], ready_events[idx], init_errors),
                name=t_name,
                daemon=True,
            )
            self._workers.append(worker)
            worker.start()

        self._worker = self._workers[0]
        for idx, ev in enumerate(ready_events):
            ev.wait(timeout=3.0)
            if init_errors[idx] is not None:
                raise RuntimeError(f"Falha ao inicializar SQLite em {self.shard_paths[idx]}") from init_errors[idx]

    @staticmethod
    def _configure_connection(conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA busy_timeout=3000;")
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA wal_autocheckpoint=1000;")
        conn.execute("PRAGMA journal_size_limit=67108864;")
        conn.execute(f"PRAGMA mmap_size={SQLITE_MMAP_SIZE_BYTES};")
        conn.execute("PRAGMA temp_store=MEMORY;")

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        self._configure_connection(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                target TEXT,
                shot_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_id_desc ON sessions(id DESC);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_target_id_desc ON sessions(target, id DESC);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at DESC);")
        conn.commit()

    def select_shard_index(self, session_id: str, target: Optional[str] = None) -> int:
        """Seleciona o shard determinístico por hash de alvo/sessão em O(1)."""
        if self.num_shards <= 1:
            return 0
        key = f"{target or ''}:{session_id}".encode("utf-8")
        digest = hashlib.blake2b(key, digest_size=4).digest()
        return int.from_bytes(digest, "big") % self.num_shards

    def queue_depth(self) -> int:
        return sum(q.qsize() for q in self._shard_queues)

    def queue_capacity(self) -> int:
        return sum(q.maxsize for q in self._shard_queues)

    def _any_shard_exists(self) -> bool:
        return any(p.exists() for p in self.shard_paths)

    def _prune_old_sessions(self, conn: sqlite3.Connection) -> None:
        """Mantém apenas os últimos registros por shard em janela deslizante."""
        limit_rows = self._per_shard_max_stored
        if limit_rows is None or limit_rows <= 0:
            return
        cutoff_row = conn.execute(
            "SELECT id FROM sessions ORDER BY id DESC LIMIT 1 OFFSET ?",
            (limit_rows - 1,),
        ).fetchone()
        if cutoff_row is not None:
            conn.execute("DELETE FROM sessions WHERE id < ?", (cutoff_row[0],))

    def _worker_loop(
        self,
        shard_idx: int,
        shard_db_path: Path,
        shard_queue: "queue.Queue[Any]",
        ready_event: threading.Event,
        init_errors: list[Optional[BaseException]],
    ) -> None:
        conn: Optional[sqlite3.Connection] = None
        try:
            shard_db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(shard_db_path), timeout=3.0)
            self._ensure_schema(conn)
        except BaseException as exc:
            init_errors[shard_idx] = exc
            ready_event.set()
            if conn is not None:
                conn.close()
            return

        ready_event.set()
        insert_sql = (
            "INSERT INTO sessions (session_id, target, shot_count, created_at, payload_json) "
            "VALUES (?, ?, ?, ?, ?)"
        )
        commits_since_prune = 0
        writes_since_prune = 0
        limit_rows = self._per_shard_max_stored
        pruning_enabled = limit_rows is not None and limit_rows > 0
        try:
            while True:
                item = shard_queue.get()
                if item is _SENTINEL:
                    if pruning_enabled and writes_since_prune > 0:
                        try:
                            self._prune_old_sessions(conn)
                            conn.commit()
                            with self._read_cache_lock:
                                self._read_cache.clear()
                                self._json_cache.clear()
                            self.cache_backend.incr_version(_SESSIONS_VERSION_KEY)
                        except Exception as exc:
                            sys.stderr.write(f"[session-store] Erro ao podar sessões no encerramento: {exc}\n")
                    shard_queue.task_done()
                    break

                batch = [item]
                saw_sentinel = False
                while len(batch) < SESSION_BATCH_SIZE:
                    try:
                        nxt = shard_queue.get_nowait()
                    except queue.Empty:
                        break
                    if nxt is _SENTINEL:
                        saw_sentinel = True
                        break
                    batch.append(nxt)

                try:
                    conn.executemany(insert_sql, batch)
                    commits_since_prune += 1
                    writes_since_prune += len(batch)
                    if pruning_enabled and (
                        commits_since_prune >= self.prune_every_commits
                        or (limit_rows is not None and writes_since_prune >= limit_rows)
                        or saw_sentinel
                    ):
                        self._prune_old_sessions(conn)
                        commits_since_prune = 0
                        writes_since_prune = 0
                    conn.commit()
                    with self._read_cache_lock:
                        self._read_cache.clear()
                        self._json_cache.clear()
                    self.cache_backend.incr_version(_SESSIONS_VERSION_KEY)
                except Exception as exc:
                    sys.stderr.write(f"[session-store] Erro ao gravar lote no SQLite: {exc}\n")
                finally:
                    for _ in batch:
                        shard_queue.task_done()
                    if saw_sentinel:
                        shard_queue.task_done()

                if saw_sentinel:
                    break
        finally:
            conn.close()

    def enqueue(self, payload: dict[str, Any]) -> str:
        """Valida minimamente e enfileira uma sessão em O(1) no shard correspondente sem bloquear o socket."""
        with self._close_lock:
            if self._closed:
                raise queue.Full("SessionStore fechado")

        raw_id = payload.get("session_id") or payload.get("sessionId") or payload.get("id")
        session_id = str(raw_id) if raw_id else uuid.uuid4().hex
        target = payload.get("target") or payload.get("target_id") or payload.get("profile")
        target_str = str(target) if target is not None else None

        shots = payload.get("shots")
        if isinstance(shots, list):
            shot_count = len(shots)
        else:
            raw_count = payload.get("shot_count", payload.get("shotCount", 0))
            try:
                shot_count = max(0, int(raw_count))
            except (TypeError, ValueError):
                shot_count = 0

        created_at = str(
            payload.get("created_at")
            or payload.get("createdAt")
            or datetime.now(timezone.utc).isoformat()
        )
        payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        record = (session_id, target_str, shot_count, created_at, payload_json)
        shard_idx = self.select_shard_index(session_id, target_str)
        self._shard_queues[shard_idx].put_nowait(record)
        return session_id

    @staticmethod
    def _query_shard_rows(
        shard_path: Path,
        clamped_limit: int,
        target: Optional[str],
        multi_shard: bool,
    ) -> list[Tuple[int, str, Optional[str], int, str, str]]:
        if not shard_path.exists():
            return []
        conn = sqlite3.connect(str(shard_path), timeout=3.0)
        try:
            conn.execute("PRAGMA busy_timeout=3000;")
            conn.execute(f"PRAGMA mmap_size={SQLITE_MMAP_SIZE_BYTES};")
            conn.execute("PRAGMA temp_store=MEMORY;")
            if target is not None:
                return conn.execute(
                    "SELECT id, session_id, target, shot_count, created_at, payload_json "
                    "FROM sessions WHERE target = ? ORDER BY id DESC LIMIT ?",
                    (target, clamped_limit),
                ).fetchall()
            if multi_shard:
                return conn.execute(
                    "SELECT id, session_id, target, shot_count, created_at, payload_json "
                    "FROM sessions ORDER BY created_at DESC, id DESC LIMIT ?",
                    (clamped_limit,),
                ).fetchall()
            return conn.execute(
                "SELECT id, session_id, target, shot_count, created_at, payload_json "
                "FROM sessions ORDER BY id DESC LIMIT ?",
                (clamped_limit,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()

    def list_sessions(self, limit: int = 50, target: Optional[str] = None) -> list[dict[str, Any]]:
        """Retorna as últimas sessões persistidas nos shards SQLite ordenadas da mais recente para a mais antiga."""
        clamped_limit = max(1, min(int(limit), 500))
        if not self._any_shard_exists():
            return []

        version = self.cache_backend.get_version(_SESSIONS_VERSION_KEY)
        cache_key = (clamped_limit, target)
        with self._read_cache_lock:
            if self._read_cache_version != version:
                self._read_cache.clear()
                self._json_cache.clear()
                self._read_cache_version = version
            cached = self._read_cache.get(cache_key)
            if cached is not None:
                return list(cached)

        multi_shard = self.num_shards > 1
        all_rows: list[Tuple[int, str, Optional[str], int, str, str]] = []
        for shard_path in self.shard_paths:
            all_rows.extend(self._query_shard_rows(shard_path, clamped_limit, target, multi_shard))

        if multi_shard and len(all_rows) > 1:
            all_rows.sort(key=lambda r: (r[4], r[0]), reverse=True)
            all_rows = all_rows[:clamped_limit]

        result: list[dict[str, Any]] = []
        for row_id, session_id, row_target, shot_count, created_at, payload_json in all_rows:
            try:
                parsed_payload = json.loads(payload_json)
            except json.JSONDecodeError:
                parsed_payload = {}
            result.append(
                {
                    "id": row_id,
                    "session_id": session_id,
                    "target": row_target,
                    "shot_count": shot_count,
                    "created_at": created_at,
                    "payload": parsed_payload,
                }
            )
        with self._read_cache_lock:
            if self._read_cache_version == version:
                self._read_cache[cache_key] = result
        return list(result)

    @staticmethod
    def _sessions_cache_prefix(version: int, clamped_limit: int, target: Optional[str]) -> str:
        if target is None:
            return f"sessions:v{version}:limit:{clamped_limit}"
        target_hash = hashlib.blake2b(target.encode("utf-8"), digest_size=6).hexdigest()
        return f"sessions:v{version}:limit:{clamped_limit}:t:{target_hash}"

    def get_sessions_meta(
        self,
        limit: int = 50,
        target: Optional[str] = None,
    ) -> Optional[Tuple[str, str, bool, int]]:
        """Retorna (etag_plain, etag_gzip, has_gzip, version) do cache L1/L2 para validação condicional O(1)."""
        clamped_limit = max(1, min(int(limit), 500))
        version = self.cache_backend.get_version(_SESSIONS_VERSION_KEY)
        prefix = self._sessions_cache_prefix(version, clamped_limit, target)
        raw_meta = self.cache_backend.get(f"{prefix}:meta")
        if not raw_meta:
            return None
        try:
            parts = raw_meta.decode("ascii").split("|")
            if len(parts) != 3:
                return None
            return parts[0], parts[1], (parts[2] == "1"), version
        except Exception:
            return None

    def get_sessions_cached(
        self,
        limit: int = 50,
        accept_gzip: bool = False,
        target: Optional[str] = None,
    ) -> Tuple[bytes, str, bool, int]:
        """Retorna (payload_bytes, etag, is_gzip, version) via CacheBackend L1/L2 invalidado por sessions:version."""
        clamped_limit = max(1, min(int(limit), 500))
        version = self.cache_backend.get_version(_SESSIONS_VERSION_KEY)
        prefix = self._sessions_cache_prefix(version, clamped_limit, target)
        meta_key = f"{prefix}:meta"
        plain_key = f"{prefix}:json"
        gzip_key = f"{prefix}:gzip"

        raw_meta = self.cache_backend.get(meta_key)
        if raw_meta:
            try:
                etag_plain, etag_gzip, has_gz_flag = raw_meta.decode("ascii").split("|")
                has_gzip = has_gz_flag == "1"
                if accept_gzip and has_gzip:
                    cached_gz = self.cache_backend.get(gzip_key)
                    if cached_gz is not None:
                        return cached_gz, etag_gzip, True, version
                else:
                    cached_plain = self.cache_backend.get(plain_key)
                    if cached_plain is not None:
                        return cached_plain, etag_plain, False, version
            except Exception:
                pass

        if not self._any_shard_exists():
            plain_bytes = b'{"sessions":[],"count":0}'
        else:
            sessions = self.list_sessions(limit=clamped_limit, target=target)
            plain_bytes = json.dumps(
                {"sessions": sessions, "count": len(sessions)},
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")

        gzip_bytes = gzip.compress(plain_bytes, compresslevel=6) if len(plain_bytes) > 256 else None
        digest = hashlib.blake2b(plain_bytes, digest_size=8).hexdigest()
        etag_plain = f'"sess-v{version}-l{clamped_limit}-{digest}-0"'
        etag_gzip = f'"sess-v{version}-l{clamped_limit}-{digest}-1"' if gzip_bytes is not None else etag_plain

        self.cache_backend.set(plain_key, plain_bytes, ttl=_SESSIONS_CACHE_TTL_SECONDS)
        if gzip_bytes is not None:
            self.cache_backend.set(gzip_key, gzip_bytes, ttl=_SESSIONS_CACHE_TTL_SECONDS)
        meta_val = f"{etag_plain}|{etag_gzip}|{1 if gzip_bytes is not None else 0}".encode("ascii")
        self.cache_backend.set(meta_key, meta_val, ttl=_SESSIONS_CACHE_TTL_SECONDS)
        self.cache_backend.set("sessions:etag:plain", etag_plain.encode("ascii"), ttl=_SESSIONS_CACHE_TTL_SECONDS)
        self.cache_backend.set("sessions:etag:gzip", etag_gzip.encode("ascii"), ttl=_SESSIONS_CACHE_TTL_SECONDS)

        with self._read_cache_lock:
            self._json_cache[(clamped_limit, target)] = plain_bytes

        if accept_gzip and gzip_bytes is not None:
            return gzip_bytes, etag_gzip, True, version
        return plain_bytes, etag_plain, False, version

    def list_sessions_json_bytes(self, limit: int = 50, target: Optional[str] = None) -> bytes:
        """Retorna o JSON serializado de list_sessions com cache L1/L2 versionado por sessions:version."""
        body, _, _, _ = self.get_sessions_cached(limit=limit, accept_gzip=False, target=target)
        return body

    def register_close_callback(self, callback: Any) -> None:
        """Registra callback executado imediatamente quando SessionStore.close() é acionado."""
        with self._close_lock:
            if self._closed:
                already_closed = True
            else:
                self._close_callbacks.append(callback)
                already_closed = False
        if already_closed:
            try:
                callback()
            except Exception:
                pass

    def close(self, timeout: float = 5.0) -> None:
        """Envia sentinela em todas as filas de shards e aguarda drenagem completa antes de encerrar."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            callbacks = list(self._close_callbacks)
            self._close_callbacks.clear()

        for cb in callbacks:
            try:
                cb()
            except Exception:
                pass

        for idx, worker in enumerate(self._workers):
            if worker.is_alive():
                self._shard_queues[idx].put(_SENTINEL)
        deadline = time.monotonic() + timeout
        for worker in self._workers:
            if worker.is_alive():
                remaining = max(0.05, deadline - time.monotonic())
                worker.join(timeout=remaining)


def _get_default_session_store() -> SessionStore:
    global _DEFAULT_SESSION_STORE
    with _DEFAULT_STORE_LOCK:
        if _DEFAULT_SESSION_STORE is None or _DEFAULT_SESSION_STORE._closed:
            _DEFAULT_SESSION_STORE = SessionStore(SESSIONS_DB_PATH, cache_backend=_get_default_cache_backend())
        return _DEFAULT_SESSION_STORE


def _drain_all_active_stores() -> None:
    with _ACTIVE_STORES_LOCK:
        stores = list(_ACTIVE_STORES)
    for store in stores:
        store.close()
    if _DEFAULT_CACHE_BACKEND is not None:
        _DEFAULT_CACHE_BACKEND.close()


atexit.register(_drain_all_active_stores)


_POOL_SENTINEL = object()


class DynamicWorkerPool:
    """
    Pool elástico de threads para 1.000+ conexões Keep-Alive simultâneas:
    - Escala sob demanda até max_workers quando não há workers ociosos.
    - Retrai threads excedentes acima de min_workers após idle_timeout segundos.
    - Fornece telemetria em O(1) para /api/health.
    """

    def __init__(
        self,
        min_workers: int = DEFAULT_MIN_WORKERS,
        max_workers: int = DEFAULT_MAX_WORKERS,
        idle_timeout: float = DEFAULT_WORKER_IDLE_TIMEOUT,
        thread_name_prefix: str = "https-worker",
    ) -> None:
        self.max_workers = max(1, int(max_workers))
        self.min_workers = max(1, min(int(min_workers), self.max_workers))
        self.idle_timeout = max(0.1, float(idle_timeout))
        self.thread_name_prefix = thread_name_prefix

        self._task_queue: "queue.SimpleQueue[Any]" = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._shutdown = False
        self._total_workers = 0
        self._idle_workers = 0
        self._active_workers = 0
        self._pending_tasks = 0
        self._peak_workers = 0
        self._seq = 0
        self._threads: set[threading.Thread] = set()

        with self._lock:
            for _ in range(self.min_workers):
                self._spawn_worker_locked()

    def _spawn_worker_locked(self) -> None:
        self._seq += 1
        self._total_workers += 1
        if self._total_workers > self._peak_workers:
            self._peak_workers = self._total_workers
        t = threading.Thread(
            target=self._worker_loop,
            name=f"{self.thread_name_prefix}-{self._seq}",
            daemon=True,
        )
        self._threads.add(t)
        t.start()

    def _worker_loop(self) -> None:
        current = threading.current_thread()
        try:
            while True:
                with self._lock:
                    if self._shutdown:
                        wait_timeout: Optional[float] = 0.05
                    elif self._total_workers > self.min_workers:
                        wait_timeout = self.idle_timeout
                    else:
                        wait_timeout = None
                    self._idle_workers += 1

                try:
                    item = self._task_queue.get(block=True, timeout=wait_timeout)
                except queue.Empty:
                    with self._lock:
                        self._idle_workers -= 1
                        if self._shutdown or self._total_workers > self.min_workers:
                            return
                        continue

                with self._lock:
                    self._idle_workers -= 1
                    if item is _POOL_SENTINEL:
                        return
                    self._pending_tasks = max(0, self._pending_tasks - 1)
                    self._active_workers += 1

                fn, args = item
                try:
                    fn(*args)
                except Exception as exc:
                    sys.stderr.write(f"[{self.thread_name_prefix}] Erro em worker: {exc}\n")
                finally:
                    with self._lock:
                        self._active_workers -= 1
        finally:
            with self._lock:
                self._total_workers = max(0, self._total_workers - 1)
                self._threads.discard(current)

    def submit(self, fn: Any, *args: Any) -> None:
        with self._lock:
            if self._shutdown:
                raise RuntimeError("DynamicWorkerPool encerrado")
            self._task_queue.put((fn, args))
            self._pending_tasks += 1
            needed = self._active_workers + self._pending_tasks
            while self._total_workers < min(needed, self.max_workers):
                self._spawn_worker_locked()

    def utilization_counts(self) -> Tuple[int, int]:
        """Leitura lock-free (active, max) para governança adaptativa de Keep-Alive no hot-path."""
        return self._active_workers, self.max_workers

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "min": self.min_workers,
                "max": self.max_workers,
                "current": self._total_workers,
                "active": self._active_workers,
                "idle": self._idle_workers,
                "peak": self._peak_workers,
            }

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        del cancel_futures
        with self._lock:
            if self._shutdown:
                threads_snap = list(self._threads)
            else:
                self._shutdown = True
                count = self._total_workers
                threads_snap = list(self._threads)
                for _ in range(count):
                    self._task_queue.put(_POOL_SENTINEL)
        if wait:
            for t in threads_snap:
                if t.is_alive():
                    t.join(timeout=15.0)


def _raise_nofile_limit(target: int = 16384) -> None:
    """Eleva o limite soft de file descriptors (RLIMIT_NOFILE) até o teto permitido pelo kernel."""
    if resource is None:
        return
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        desired = min(max(int(soft), int(target)), int(hard)) if hard > 0 else max(int(soft), int(target))
        if desired > soft:
            resource.setrlimit(resource.RLIMIT_NOFILE, (desired, hard))
    except (ValueError, OSError):
        pass


def _resolve_max_workers(
    max_workers: Optional[int] = None,
    default_workers: int = DEFAULT_MAX_WORKERS,
) -> int:
    if max_workers is not None:
        return max(1, int(max_workers))
    raw = os.environ.get("TARGETS_MAX_WORKERS", "").strip()
    if not raw:
        return default_workers
    try:
        return max(1, int(raw))
    except ValueError:
        return default_workers


class ThreadedHTTPSServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = SOCKET_BACKLOG_SIZE

    def __init__(
        self,
        server_address: Tuple[str, int],
        RequestHandlerClass: type[SimpleHTTPRequestHandler],
        bind_and_activate: bool = True,
        max_workers: Optional[int] = None,
        min_workers: Optional[int] = None,
        reuse_port: bool = True,
        db_path: Optional[Path] = None,
        session_store: Optional[SessionStore] = None,
        cache_backend: Optional[CacheBackend] = None,
        num_shards: Optional[int] = None,
    ) -> None:
        _raise_nofile_limit()
        clear_route_fast_cache()
        eff_max = _resolve_max_workers(max_workers)
        eff_min = min(eff_max, DEFAULT_MIN_WORKERS if min_workers is None else max(1, int(min_workers)))
        self.reuse_port = bool(reuse_port) and hasattr(socket, "SO_REUSEPORT")
        self._started_at = time.monotonic()
        self._executor = DynamicWorkerPool(
            min_workers=eff_min,
            max_workers=eff_max,
            thread_name_prefix="https-worker",
        )
        self._close_lock = threading.Lock()
        self._server_closed = False
        self._health_refresh_lock = threading.Lock()
        self._health_cache: Optional[Tuple[float, int, bytes, dict[str, Any], str, bool]] = None
        self._owns_session_store = session_store is None
        resolved_db_path = Path(db_path) if db_path is not None else SESSIONS_DB_PATH
        if cache_backend is not None:
            self.cache_backend = cache_backend
            self._owns_cache_backend = False
        elif session_store is not None:
            self.cache_backend = session_store.cache_backend
            self._owns_cache_backend = False
        elif resolved_db_path.resolve() != SESSIONS_DB_PATH:
            self.cache_backend = CacheBackend(enable_redis=False)
            self._owns_cache_backend = True
        else:
            self.cache_backend = _get_default_cache_backend()
            self._owns_cache_backend = False

        self.session_store = (
            session_store
            if session_store is not None
            else SessionStore(
                db_path=resolved_db_path,
                cache_backend=self.cache_backend,
                num_shards=num_shards,
            )
        )
        srv_ref = weakref.ref(self)

        def _on_store_closed() -> None:
            srv = srv_ref()
            if srv is not None:
                srv.invalidate_health_cache()

        self.session_store.register_close_callback(_on_store_closed)
        super().__init__(server_address, RequestHandlerClass, bind_and_activate=bind_and_activate)

    def server_bind(self) -> None:
        if self.reuse_port and hasattr(socket, "SO_REUSEPORT"):
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        try:
            self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        super().server_bind()

    @staticmethod
    def _configure_client_socket(sock: socket.socket) -> None:
        """Configura opções de baixa latência (TCP_NODELAY e SO_KEEPALIVE) logo na aceitação."""
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass

    def get_request(self) -> Tuple[socket.socket, Any]:
        request, client_address = super().get_request()
        self._configure_client_socket(request)
        return request, client_address

    def wrap_ssl(self, ctx: ssl.SSLContext, do_handshake_on_connect: bool = False) -> None:
        """Envolve o socket com TLS permitindo handshake paralelo nos workers."""
        self.socket = ctx.wrap_socket(
            self.socket,
            server_side=True,
            do_handshake_on_connect=do_handshake_on_connect,
        )

    def invalidate_health_cache(self) -> None:
        """Invalida imediatamente o snapshot pré-serializado de /api/health."""
        self._health_cache = None

    def _build_health_snapshot_entry(self) -> Tuple[float, int, bytes, dict[str, Any], str, bool]:
        workers_stats = self._executor.stats()
        depth = self.session_store.queue_depth()
        capacity = max(1, self.session_store.queue_capacity())
        utilization = round((depth / capacity) * 100.0, 2)
        store_closed = bool(self.session_store._closed or self._server_closed)

        if store_closed:
            status = "unhealthy"
            http_status = 503
        elif depth >= int(capacity * 0.90):
            status = "degraded"
            http_status = 200
        else:
            status = "ok"
            http_status = 200

        cache_stats = self.cache_backend.stats()
        cache_stats["sessions_version"] = self.cache_backend.get_local_version(_SESSIONS_VERSION_KEY)

        payload = {
            "status": status,
            "stateless": True,
            "pid": os.getpid(),
            "uptime_s": round(max(0.0, time.monotonic() - self._started_at), 3),
            "reuse_port": self.reuse_port,
            "request_queue_size": self.request_queue_size,
            "workers": workers_stats,
            "queue": {
                "depth": depth,
                "capacity": capacity,
                "utilization_pct": utilization,
                "shards": self.session_store.num_shards,
                "batch_size": SESSION_BATCH_SIZE,
                "closed": store_closed,
            },
            "cache": cache_stats,
        }
        raw_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        expires_at = time.monotonic() + HEALTH_CACHE_TTL_SECONDS
        return expires_at, http_status, raw_bytes, payload, str(len(raw_bytes)), store_closed

    def _get_health_snapshot_entry(self) -> Tuple[float, int, bytes, dict[str, Any], str, bool]:
        now = time.monotonic()
        current_closed = bool(self._server_closed or self.session_store._closed)
        snap = self._health_cache
        if snap is not None and now < snap[0] and snap[5] == current_closed:
            return snap

        with self._health_refresh_lock:
            now = time.monotonic()
            current_closed = bool(self._server_closed or self.session_store._closed)
            snap = self._health_cache
            if snap is not None and now < snap[0] and snap[5] == current_closed:
                return snap
            entry = self._build_health_snapshot_entry()
            self._health_cache = entry
            return entry

    def health_snapshot_bytes(self) -> Tuple[int, memoryview, str]:
        """Retorna (http_status, memoryview_json, content_length) com micro-TTL de 50 ms sem locks no hot-path."""
        entry = self._get_health_snapshot_entry()
        return entry[1], memoryview(entry[2]), entry[4]

    def health_snapshot(self) -> Tuple[int, dict[str, Any]]:
        """Retorna telemetria de saúde em O(1) para sondas de Load Balancer e Auto Scaling."""
        entry = self._get_health_snapshot_entry()
        return entry[1], entry[3]

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        """Submete a requisição ao pool elástico de threads."""
        try:
            self._executor.submit(self.process_request_thread, request, client_address)
        except RuntimeError:
            self.shutdown_request(request)

    def server_close(self) -> None:
        with self._close_lock:
            if self._server_closed:
                return
            self._server_closed = True
        self.invalidate_health_cache()

        super().server_close()
        self._executor.shutdown(wait=True, cancel_futures=False)
        if self._owns_session_store and self.session_store is not None:
            self.session_store.close()
        if getattr(self, "_owns_cache_backend", False) and self.cache_backend is not None:
            self.cache_backend.close()


_TARGETS_CACHE_LOCK = threading.Lock()
_TARGETS_CACHE: dict[str, Any] = {
    "dir_mtime_ns": None,
    "checked_at": 0.0,
    "force_gzip": None,
    "json_bytes": None,
    "gzip_bytes": None,
    "etag_plain": None,
    "etag_gzip": None,
}

_STATIC_CACHE_LOCK = threading.Lock()
_STATIC_MEM_CACHE: "OrderedDict[Tuple[str, int, int], bytes]" = OrderedDict()
_STATIC_CACHE_MAX_BYTES = 16 * 1024 * 1024
_STATIC_CACHE_MAX_FILE_BYTES = 2 * 1024 * 1024
_STATIC_CACHE_CUR_BYTES = 0

_ACCEPT_ENCODING_CACHE_LOCK = threading.Lock()
_ACCEPT_ENCODING_CACHE: dict[str, str] = {}
_ACCEPT_ENCODING_CACHE_MAX = 256


class _RouteFastEntry(NamedTuple):
    etag: str
    content_type: str
    content_encoding: Optional[str]
    cache_control: Optional[str]
    body_bytes: Optional[bytes]
    content_length: str
    vary_encoding: bool
    file_path: Path
    force_gzip: bool
    expires_at: Optional[float]


_ROUTE_FAST_CACHE_LOCK = threading.Lock()
_ROUTE_FAST_CACHE: "OrderedDict[Tuple[str, str], _RouteFastEntry]" = OrderedDict()
_ROUTE_FAST_CACHE_CUR_BYTES = 0
_ROUTE_FAST_CACHE_MAX_BYTES = 32 * 1024 * 1024
_ROUTE_FAST_CACHE_MAX_ENTRIES = 512
_ROUTE_FAST_DEV_TTL_SECONDS = 1.0


def clear_route_fast_cache() -> None:
    """Limpa a tabela de roteamento em RAM, o cache de /api/targets e o L1 do cache padrão."""
    global _ROUTE_FAST_CACHE_CUR_BYTES
    with _ROUTE_FAST_CACHE_LOCK:
        _ROUTE_FAST_CACHE.clear()
        _ROUTE_FAST_CACHE_CUR_BYTES = 0
    with _TARGETS_CACHE_LOCK:
        _TARGETS_CACHE["dir_mtime_ns"] = None
        _TARGETS_CACHE["checked_at"] = 0.0
        _TARGETS_CACHE["force_gzip"] = None
        _TARGETS_CACHE["json_bytes"] = None
        _TARGETS_CACHE["gzip_bytes"] = None
        _TARGETS_CACHE["etag_plain"] = None
        _TARGETS_CACHE["etag_gzip"] = None
    if _DEFAULT_CACHE_BACKEND is not None:
        _DEFAULT_CACHE_BACKEND.clear_l1()


def _get_route_fast_cache(key: Tuple[str, str], force_gzip: bool) -> Optional[_RouteFastEntry]:
    with _ROUTE_FAST_CACHE_LOCK:
        entry = _ROUTE_FAST_CACHE.get(key)
        if entry is None:
            return None
        if entry.force_gzip != force_gzip:
            return None
        if entry.expires_at is not None and time.monotonic() >= entry.expires_at:
            return None
        if len(_ROUTE_FAST_CACHE) > 256:
            _ROUTE_FAST_CACHE.move_to_end(key)
        return entry


def _put_route_fast_cache(key: Tuple[str, str], entry: _RouteFastEntry) -> None:
    global _ROUTE_FAST_CACHE_CUR_BYTES
    entry_size = len(entry.body_bytes) if entry.body_bytes is not None else 0
    with _ROUTE_FAST_CACHE_LOCK:
        old = _ROUTE_FAST_CACHE.pop(key, None)
        if old is not None and old.body_bytes is not None:
            _ROUTE_FAST_CACHE_CUR_BYTES -= len(old.body_bytes)

        while (
            (
                _ROUTE_FAST_CACHE_CUR_BYTES + entry_size > _ROUTE_FAST_CACHE_MAX_BYTES
                or len(_ROUTE_FAST_CACHE) >= _ROUTE_FAST_CACHE_MAX_ENTRIES
            )
            and _ROUTE_FAST_CACHE
        ):
            _, evicted = _ROUTE_FAST_CACHE.popitem(last=False)
            if evicted.body_bytes is not None:
                _ROUTE_FAST_CACHE_CUR_BYTES -= len(evicted.body_bytes)

        _ROUTE_FAST_CACHE[key] = entry
        _ROUTE_FAST_CACHE_CUR_BYTES += entry_size


def _pack_targets_bundle(
    json_bytes: bytes,
    gzip_bytes: Optional[bytes],
    etag_plain: str,
    etag_gzip: str,
) -> bytes:
    gz = gzip_bytes or b""
    ep = etag_plain.encode("ascii")
    eg = etag_gzip.encode("ascii")
    header = struct.pack(">IIII", len(json_bytes), len(gz), len(ep), len(eg))
    return header + json_bytes + gz + ep + eg


def _unpack_targets_bundle(raw: bytes) -> Optional[Tuple[bytes, Optional[bytes], str, str]]:
    if len(raw) < 16:
        return None
    j_len, g_len, ep_len, eg_len = struct.unpack_from(">IIII", raw, 0)
    if 16 + j_len + g_len + ep_len + eg_len != len(raw):
        return None
    pos = 16
    json_bytes = raw[pos : pos + j_len]
    pos += j_len
    gzip_bytes = raw[pos : pos + g_len] if g_len > 0 else None
    pos += g_len
    etag_plain = raw[pos : pos + ep_len].decode("ascii")
    pos += ep_len
    etag_gzip = raw[pos : pos + eg_len].decode("ascii")
    return json_bytes, gzip_bytes, etag_plain, etag_gzip


def _get_targets_cached(
    force_gzip: bool = False,
    cache_backend: Optional[CacheBackend] = None,
) -> Tuple[bytes, Optional[bytes], str, str]:
    """Retorna payload de /api/targets cacheado em L1 RAM Zero-Copy + L2 Redis sem syscalls no hot-path."""
    now = time.monotonic()
    with _TARGETS_CACHE_LOCK:
        if _TARGETS_CACHE["json_bytes"] is not None:
            if force_gzip and _TARGETS_CACHE["force_gzip"] is True:
                return (
                    _TARGETS_CACHE["json_bytes"],
                    _TARGETS_CACHE["gzip_bytes"],
                    _TARGETS_CACHE["etag_plain"],
                    _TARGETS_CACHE["etag_gzip"],
                )
            if not force_gzip and (now - _TARGETS_CACHE["checked_at"]) < _ROUTE_FAST_DEV_TTL_SECONDS:
                return (
                    _TARGETS_CACHE["json_bytes"],
                    _TARGETS_CACHE["gzip_bytes"],
                    _TARGETS_CACHE["etag_plain"],
                    _TARGETS_CACHE["etag_gzip"],
                )

    try:
        dir_mtime_ns = os.stat(MANTIS_IMAGES_DIR).st_mtime_ns
    except OSError:
        dir_mtime_ns = -1

    with _TARGETS_CACHE_LOCK:
        if _TARGETS_CACHE["dir_mtime_ns"] == dir_mtime_ns and _TARGETS_CACHE["json_bytes"] is not None:
            _TARGETS_CACHE["checked_at"] = now
            _TARGETS_CACHE["force_gzip"] = force_gzip
            return (
                _TARGETS_CACHE["json_bytes"],
                _TARGETS_CACHE["gzip_bytes"],
                _TARGETS_CACHE["etag_plain"],
                _TARGETS_CACHE["etag_gzip"],
            )

    backend = cache_backend if cache_backend is not None else _get_default_cache_backend()
    bundle_key = f"targets:bundle:{dir_mtime_ns}"
    cached_bundle = backend.get(bundle_key)
    if cached_bundle is not None:
        unpacked = _unpack_targets_bundle(cached_bundle)
        if unpacked is not None:
            json_bytes, gzip_bytes, etag_plain, etag_gzip = unpacked
            with _TARGETS_CACHE_LOCK:
                _TARGETS_CACHE["dir_mtime_ns"] = dir_mtime_ns
                _TARGETS_CACHE["checked_at"] = now
                _TARGETS_CACHE["force_gzip"] = force_gzip
                _TARGETS_CACHE["json_bytes"] = json_bytes
                _TARGETS_CACHE["gzip_bytes"] = gzip_bytes
                _TARGETS_CACHE["etag_plain"] = etag_plain
                _TARGETS_CACHE["etag_gzip"] = etag_gzip
            return json_bytes, gzip_bytes, etag_plain, etag_gzip

    targets = []
    if dir_mtime_ns != -1:
        for f in sorted(MANTIS_IMAGES_DIR.glob("*.png")):
            name = f.stem.replace("Mantis_LaserAcademy_Targets_2_2026-06-10-", "Mantis_Target_")
            targets.append({
                "id": f.stem,
                "name": name,
                "url": f"/mantis_images/{f.name}",
            })

    json_bytes = json.dumps(targets).encode("utf-8")
    gzip_bytes = gzip.compress(json_bytes, compresslevel=6) if len(json_bytes) > 256 else None
    digest = hashlib.blake2b(json_bytes, digest_size=8).hexdigest()
    etag_plain = f'"{digest}-0"'
    etag_gzip = f'"{digest}-1"' if gzip_bytes is not None else etag_plain

    bundle_ttl = None if force_gzip else 60.0
    backend.set(bundle_key, _pack_targets_bundle(json_bytes, gzip_bytes, etag_plain, etag_gzip), ttl=bundle_ttl)
    backend.set("targets:etag:plain", etag_plain.encode("ascii"), ttl=bundle_ttl)
    backend.set("targets:etag:gzip", etag_gzip.encode("ascii"), ttl=bundle_ttl)

    with _TARGETS_CACHE_LOCK:
        _TARGETS_CACHE["dir_mtime_ns"] = dir_mtime_ns
        _TARGETS_CACHE["checked_at"] = now
        _TARGETS_CACHE["force_gzip"] = force_gzip
        _TARGETS_CACHE["json_bytes"] = json_bytes
        _TARGETS_CACHE["gzip_bytes"] = gzip_bytes
        _TARGETS_CACHE["etag_plain"] = etag_plain
        _TARGETS_CACHE["etag_gzip"] = etag_gzip

    return json_bytes, gzip_bytes, etag_plain, etag_gzip


def _read_static_cached_bytes(file_path: Path, st: os.stat_result) -> Optional[bytes]:
    global _STATIC_CACHE_CUR_BYTES
    if st.st_size > _STATIC_CACHE_MAX_FILE_BYTES:
        return None

    key = (str(file_path), st.st_mtime_ns, st.st_size)
    with _STATIC_CACHE_LOCK:
        cached = _STATIC_MEM_CACHE.get(key)
        if cached is not None:
            _STATIC_MEM_CACHE.move_to_end(key)
            return cached

    data = file_path.read_bytes()
    data_len = len(data)
    with _STATIC_CACHE_LOCK:
        if key not in _STATIC_MEM_CACHE:
            while _STATIC_CACHE_CUR_BYTES + data_len > _STATIC_CACHE_MAX_BYTES and _STATIC_MEM_CACHE:
                _, evicted = _STATIC_MEM_CACHE.popitem(last=False)
                _STATIC_CACHE_CUR_BYTES -= len(evicted)
            _STATIC_MEM_CACHE[key] = data
            _STATIC_CACHE_CUR_BYTES += data_len
        return _STATIC_MEM_CACHE[key]


def _read_static_cached(file_path: Path, st: os.stat_result) -> Optional[memoryview]:
    data = _read_static_cached_bytes(file_path, st)
    return memoryview(data) if data is not None else None


_DATE_HEADER_CACHE: Tuple[int, str] = (-1, "")


class DevRequestHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    wbufsize = 65536
    disable_nagle_algorithm = True
    force_gzip_dir = False
    quiet_logs = False

    def setup(self) -> None:
        self._handshake_failed = False
        self._cache_control_override: Optional[str] = None
        self._keepalive_requests = 0
        self._current_socket_timeout = SOCKET_TIMEOUT_SECONDS
        # Evita sockets mobile travarem indefinidamente o pool de threads
        try:
            self.request.settimeout(SOCKET_TIMEOUT_SECONDS)
        except Exception:
            pass
        if isinstance(self.request, ssl.SSLSocket) and self.request.cipher() is None:
            try:
                self.request.do_handshake()
            except (ssl.SSLError, BrokenPipeError, ConnectionResetError, TimeoutError, EOFError, OSError):
                self._handshake_failed = True
                self.close_connection = True
                return
        super().setup()

    def handle(self) -> None:
        if getattr(self, "_handshake_failed", False):
            return
        super().handle()

    def finish(self) -> None:
        if getattr(self, "_handshake_failed", False):
            return
        try:
            super().finish()
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError, TimeoutError, OSError):
            pass

    def date_time_string(self, timestamp: Optional[float] = None) -> str:
        """Retorna o cabeçalho HTTP Date cacheado a 1 Hz para eliminar time.gmtime() por request."""
        global _DATE_HEADER_CACHE
        if timestamp is not None:
            return email.utils.formatdate(timestamp, usegmt=True)
        now_sec = int(time.time())
        cached_sec, cached_str = _DATE_HEADER_CACHE
        if now_sec == cached_sec:
            return cached_str
        formatted = email.utils.formatdate(now_sec, usegmt=True)
        _DATE_HEADER_CACHE = (now_sec, formatted)
        return formatted

    def address_string(self) -> str:
        """Retorna o IP diretamente sem bloquear em reverse DNS lookup."""
        return str(self.client_address[0])

    def _executor_utilization(self) -> Tuple[int, int]:
        executor = getattr(self.server, "_executor", None)
        if executor is None:
            return 0, 0
        if hasattr(executor, "utilization_counts"):
            return executor.utilization_counts()
        if hasattr(executor, "stats"):
            wstats = executor.stats()
            return wstats["active"], wstats["max"]
        return 0, 0

    def _compute_keepalive_timeout(self) -> float:
        active_w, max_w = self._executor_utilization()
        if max_w >= 16 and active_w > int(max_w * KEEPALIVE_ADAPTIVE_THRESHOLD):
            return KEEPALIVE_PRESSURE_TIMEOUT_SECONDS
        return SOCKET_TIMEOUT_SECONDS

    def _apply_adaptive_keepalive_timeout(self) -> None:
        target_timeout = self._compute_keepalive_timeout()
        if target_timeout != getattr(self, "_current_socket_timeout", SOCKET_TIMEOUT_SECONDS):
            try:
                self.request.settimeout(target_timeout)
                self._current_socket_timeout = target_timeout
            except OSError:
                pass

    def handle_one_request(self) -> None:
        self._cache_control_override = None
        if getattr(self, "_keepalive_requests", 0) > 0:
            self._apply_adaptive_keepalive_timeout()
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError, TimeoutError):
            self.close_connection = True
            return

        if not self.close_connection:
            self._keepalive_requests = getattr(self, "_keepalive_requests", 0) + 1
            self._apply_adaptive_keepalive_timeout()

    def _get_session_store(self) -> SessionStore:
        store = getattr(self.server, "session_store", None)
        if isinstance(store, SessionStore):
            return store
        return _get_default_session_store()

    def _get_cache_backend(self) -> CacheBackend:
        backend = getattr(self.server, "cache_backend", None)
        if isinstance(backend, CacheBackend):
            return backend
        return self._get_session_store().cache_backend

    def _safe_join(self, root_dir: Path, rel_path: str) -> Optional[Path]:
        clean_rel = rel_path.lstrip("/").replace("\\", "/")
        candidate = (root_dir / clean_rel).resolve()
        if not candidate.is_relative_to(root_dir):
            return None
        return candidate

    def _resolve_static_paths(
        self, url_path: str
    ) -> Tuple[Optional[Path], Optional[Path], Optional[Path], bool]:
        path_clean = url_path.split("?", 1)[0]
        if path_clean in ("", "/"):
            path_clean = "/index.html"

        if path_clean.startswith("/lib/"):
            p = self._safe_join(LIB_DIR, path_clean[len("/lib/"):])
            return p, None, None, False

        if path_clean.startswith("/mantis_images/"):
            p = self._safe_join(MANTIS_IMAGES_DIR, path_clean[len("/mantis_images/"):])
            return p, None, None, True

        src_file = self._safe_join(EDIT_SRC, path_clean)
        rel_clean = path_clean.lstrip("/")
        br_file = self._safe_join(DATA_DIR, f"{rel_clean}.br")
        gz_file = self._safe_join(DATA_DIR, f"{rel_clean}.gz")
        return src_file, br_file, gz_file, False

    def translate_path(self, path: str) -> str:
        src_file, _, _, _ = self._resolve_static_paths(path)
        if src_file is None:
            return str(EDIT_SRC / "__forbidden__")
        return str(src_file)

    def _accepted_encodings(self) -> set[str]:
        raw = (self.headers.get("Accept-Encoding") or "").lower()
        accepted: set[str] = set()
        for part in raw.split(","):
            token = part.strip()
            if not token:
                continue
            pieces = [p.strip() for p in token.split(";")]
            enc = pieces[0]
            disabled = False
            for param in pieces[1:]:
                if param.startswith("q="):
                    try:
                        if float(param[2:]) <= 0.0:
                            disabled = True
                    except ValueError:
                        pass
            if not disabled and enc:
                accepted.add(enc)
        return accepted

    def _get_enc_preference(self) -> str:
        raw = self.headers.get("Accept-Encoding")
        if not raw:
            return ""
        cached = _ACCEPT_ENCODING_CACHE.get(raw)
        if cached is not None:
            return cached

        accepted = self._accepted_encodings()
        has_br = "br" in accepted
        has_gzip = "gzip" in accepted
        if has_br and has_gzip:
            pref = "br+gzip"
        elif has_br:
            pref = "br"
        elif has_gzip:
            pref = "gzip"
        else:
            pref = ""

        with _ACCEPT_ENCODING_CACHE_LOCK:
            if len(_ACCEPT_ENCODING_CACHE) >= _ACCEPT_ENCODING_CACHE_MAX:
                _ACCEPT_ENCODING_CACHE.clear()
            _ACCEPT_ENCODING_CACHE[raw] = pref
        return pref

    def _can_serve_compressed(self, src_file: Optional[Path], comp_file: Optional[Path]) -> bool:
        if comp_file is None or not comp_file.is_file():
            return False
        if self.force_gzip_dir:
            return True
        if src_file is not None and src_file.is_file():
            return comp_file.stat().st_mtime_ns >= src_file.stat().st_mtime_ns
        return True

    def _select_compressed_file_for_pref(
        self,
        src_file: Optional[Path],
        br_file: Optional[Path],
        gz_file: Optional[Path],
        enc_pref: str,
    ) -> Tuple[Optional[Path], Optional[str]]:
        if "br" in enc_pref and self._can_serve_compressed(src_file, br_file):
            return br_file, "br"
        if "gzip" in enc_pref and self._can_serve_compressed(src_file, gz_file):
            return gz_file, "gzip"
        return None, None

    def _select_compressed_file(
        self,
        src_file: Optional[Path],
        br_file: Optional[Path],
        gz_file: Optional[Path],
    ) -> Tuple[Optional[Path], Optional[str]]:
        return self._select_compressed_file_for_pref(
            src_file,
            br_file,
            gz_file,
            self._get_enc_preference(),
        )

    def _etag_matches(self, etag: str) -> bool:
        inm = self.headers.get("If-None-Match")
        if not inm:
            return False
        if inm == etag:
            return True
        inm = inm.strip()
        if inm == "*":
            return True
        norm_target = etag[2:] if etag.startswith("W/") else etag
        for token in inm.split(","):
            t = token.strip()
            if t.startswith("W/"):
                t = t[2:].strip()
            if t == norm_target:
                return True
        return False

    def _send_304(
        self,
        etag: str,
        vary_encoding: bool = True,
        cache_control: Optional[str] = None,
    ) -> None:
        self._cache_control_override = cache_control
        self.send_response(304)
        self.send_header("ETag", etag)
        if vary_encoding:
            self.send_header("Vary", "Accept-Encoding")
        self.end_headers()

    def _send_json(
        self,
        status_code: int,
        payload: dict[str, Any],
        cache_control: str = "no-cache, no-store",
    ) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self._cache_control_override = cache_control
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(memoryview(body))

    def _send_file_bytes(
        self,
        file_path: Path,
        content_type: str,
        encoding: Optional[str] = None,
        head_only: bool = False,
        cache_control: Optional[str] = None,
        vary_encoding: bool = True,
    ) -> None:
        st = file_path.stat()
        enc_tag = "br" if encoding == "br" else ("1" if encoding == "gzip" else "0")
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}-{enc_tag}"'
        if self._etag_matches(etag):
            self._send_304(etag, vary_encoding=vary_encoding, cache_control=cache_control)
            return

        size = st.st_size
        self._cache_control_override = cache_control
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("ETag", etag)
        if vary_encoding:
            self.send_header("Vary", "Accept-Encoding")
        if encoding is not None:
            self.send_header("Content-Encoding", encoding)
        self.end_headers()
        if not head_only:
            cached_view = _read_static_cached(file_path, st)
            if cached_view is not None:
                self.wfile.write(cached_view)
            else:
                with open(file_path, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)

    def _send_route_entry(self, entry: _RouteFastEntry, head_only: bool = False) -> None:
        if self._etag_matches(entry.etag):
            self._send_304(
                entry.etag,
                vary_encoding=entry.vary_encoding,
                cache_control=entry.cache_control,
            )
            return

        self._cache_control_override = entry.cache_control
        self.send_response(200)
        self.send_header("Content-Type", entry.content_type)
        self.send_header("Content-Length", entry.content_length)
        self.send_header("ETag", entry.etag)
        if entry.vary_encoding:
            self.send_header("Vary", "Accept-Encoding")
        if entry.content_encoding is not None:
            self.send_header("Content-Encoding", entry.content_encoding)
        self.end_headers()
        if not head_only:
            if entry.body_bytes is not None:
                self.wfile.write(memoryview(entry.body_bytes))
            else:
                with open(entry.file_path, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)

    def _handle_api_targets(self, head_only: bool = False) -> None:
        json_bytes, gzip_bytes, etag_plain, etag_gzip = _get_targets_cached(
            force_gzip=self.force_gzip_dir,
            cache_backend=self._get_cache_backend(),
        )
        enc_pref = self._get_enc_preference()
        use_gzip = ("gzip" in enc_pref) and gzip_bytes is not None
        payload = gzip_bytes if use_gzip else json_bytes
        assert payload is not None
        etag = etag_gzip if use_gzip else etag_plain

        if self._etag_matches(etag):
            self._send_304(etag, vary_encoding=True)
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("ETag", etag)
        self.send_header("Vary", "Accept-Encoding")
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        if not head_only:
            self.wfile.write(memoryview(payload))

    def _handle_get_sessions(self, head_only: bool = False) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        limit = 50
        if "limit" in qs and qs["limit"]:
            try:
                limit = int(qs["limit"][0])
            except ValueError:
                self._send_json(400, {"error": "Parâmetro limit inválido"})
                return
            if limit <= 0:
                self._send_json(400, {"error": "Parâmetro limit deve ser positivo"})
                return

        target_filter: Optional[str] = None
        if "target" in qs and qs["target"]:
            raw_t = qs["target"][0].strip()
            if raw_t:
                target_filter = raw_t

        store = self._get_session_store()
        enc_pref = self._get_enc_preference()
        accept_gzip = "gzip" in enc_pref

        if self.headers.get("If-None-Match"):
            meta = store.get_sessions_meta(limit=limit, target=target_filter)
            if meta is not None:
                etag_plain, etag_gzip, has_gzip, _ = meta
                expected_etag = etag_gzip if (accept_gzip and has_gzip) else etag_plain
                if self._etag_matches(expected_etag):
                    self._send_304(expected_etag, vary_encoding=True, cache_control="no-cache, must-revalidate")
                    return

        body, etag, use_gzip, _ = store.get_sessions_cached(
            limit=limit,
            accept_gzip=accept_gzip,
            target=target_filter,
        )
        if self._etag_matches(etag):
            self._send_304(etag, vary_encoding=True, cache_control="no-cache, must-revalidate")
            return

        self._cache_control_override = "no-cache, must-revalidate"
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Vary", "Accept-Encoding")
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        if not head_only:
            self.wfile.write(memoryview(body))

    def _handle_post_sessions(self) -> None:
        raw_cl = self.headers.get("Content-Length")
        if raw_cl is None:
            self.close_connection = True
            self._send_json(400, {"error": "Header Content-Length obrigatório"})
            return

        try:
            content_length = int(raw_cl.strip())
        except ValueError:
            self.close_connection = True
            self._send_json(400, {"error": "Content-Length inválido"})
            return

        if content_length <= 0:
            self.close_connection = True
            self._send_json(400, {"error": "Payload vazio"})
            return

        if content_length > MAX_SESSION_PAYLOAD_BYTES:
            self.close_connection = True
            self._send_json(
                413,
                {"error": "Payload Too Large", "max_bytes": MAX_SESSION_PAYLOAD_BYTES},
            )
            return

        raw_body = self.rfile.read(content_length)
        if len(raw_body) != content_length:
            self.close_connection = True
            self._send_json(400, {"error": "Corpo da requisição incompleto"})
            return

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "JSON inválido"})
            return

        if not isinstance(payload, dict):
            self._send_json(400, {"error": "JSON deve ser um objeto"})
            return

        try:
            session_id = self._get_session_store().enqueue(payload)
        except queue.Full:
            self._send_json(503, {"error": "Service Unavailable", "detail": "Fila de sessões cheia"})
            return

        self._send_json(202, {"status": "accepted", "session_id": session_id})

    def _handle_api_health(self, head_only: bool = False) -> None:
        if hasattr(self.server, "health_snapshot_bytes"):
            status_code, body_view, content_length = self.server.health_snapshot_bytes()
        elif hasattr(self.server, "health_snapshot"):
            status_code, payload = self.server.health_snapshot()
            raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            body_view = memoryview(raw)
            content_length = str(len(raw))
        else:
            store = self._get_session_store()
            cache_stats = self._get_cache_backend().stats()
            cache_stats["sessions_version"] = self._get_cache_backend().get_local_version(_SESSIONS_VERSION_KEY)
            status_code = 503 if store._closed else 200
            payload = {
                "status": "unhealthy" if store._closed else "ok",
                "stateless": True,
                "pid": os.getpid(),
                "queue": {
                    "depth": store.queue_depth(),
                    "capacity": store.queue_capacity(),
                    "shards": store.num_shards,
                    "batch_size": SESSION_BATCH_SIZE,
                    "closed": store._closed,
                },
                "cache": cache_stats,
            }
            raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            body_view = memoryview(raw)
            content_length = str(len(raw))

        self._cache_control_override = "no-cache, no-store"
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", content_length)
        self.end_headers()
        if not head_only:
            self.wfile.write(body_view)

    def _serve_request(self, head_only: bool = False) -> None:
        q_idx = self.path.find("?")
        path_clean = self.path[:q_idx] if q_idx != -1 else self.path
        if path_clean.startswith("/api/"):
            if path_clean == "/api/health" or path_clean == "/api/health/":
                self._handle_api_health(head_only=head_only)
                return
            if path_clean.startswith("/api/targets"):
                self._handle_api_targets(head_only=head_only)
                return
            if path_clean == "/api/sessions" or path_clean == "/api/sessions/":
                self._handle_get_sessions(head_only=head_only)
                return

        route_path = "/index.html" if path_clean in ("", "/") else path_clean
        unsafe_path = ".." in route_path or "\\" in route_path or "\x00" in route_path
        enc_pref = self._get_enc_preference()
        force_gzip = self.force_gzip_dir

        if not unsafe_path:
            cached_entry = _get_route_fast_cache((route_path, enc_pref), force_gzip=force_gzip)
            if cached_entry is not None:
                try:
                    self._send_route_entry(cached_entry, head_only=head_only)
                except (BrokenPipeError, ConnectionResetError, TimeoutError, ssl.SSLError):
                    pass
                return

        src_file, br_file, gz_file, is_immutable = self._resolve_static_paths(route_path)
        if src_file is None:
            self.send_error(403, "Forbidden")
            return

        if src_file.is_dir():
            src_file = src_file / "index.html"
            br_file = DATA_DIR / "index.html.br"
            gz_file = DATA_DIR / "index.html.gz"

        mime_type, _ = mimetypes.guess_type(str(src_file))
        if not mime_type:
            mime_type = "application/octet-stream"
        if mime_type.startswith("text/") or mime_type == "application/javascript":
            mime_type = f"{mime_type}; charset=utf-8"

        cache_control = "public, max-age=86400, immutable" if is_immutable else None

        try:
            comp_file, encoding = self._select_compressed_file_for_pref(
                src_file, br_file, gz_file, enc_pref
            )
            if comp_file is not None and encoding is not None:
                target_file = comp_file
                vary_encoding = True
            elif src_file.is_file():
                target_file = src_file
                encoding = None
                vary_encoding = not is_immutable
            else:
                self.send_error(404, "File not found")
                return

            try:
                st = target_file.stat()
            except OSError:
                self.send_error(404, "File not found")
                return

            enc_tag = "br" if encoding == "br" else ("1" if encoding == "gzip" else "0")
            etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}-{enc_tag}"'
            body_bytes = _read_static_cached_bytes(target_file, st)
            content_length = str(len(body_bytes)) if body_bytes is not None else str(st.st_size)
            expires_at = None if force_gzip else (time.monotonic() + _ROUTE_FAST_DEV_TTL_SECONDS)

            entry = _RouteFastEntry(
                etag=etag,
                content_type=mime_type,
                content_encoding=encoding,
                cache_control=cache_control,
                body_bytes=body_bytes,
                content_length=content_length,
                vary_encoding=vary_encoding,
                file_path=target_file,
                force_gzip=force_gzip,
                expires_at=expires_at,
            )
            if not unsafe_path:
                _put_route_fast_cache((route_path, enc_pref), entry)
            self._send_route_entry(entry, head_only=head_only)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, ssl.SSLError):
            pass

    def do_GET(self) -> None:
        self._serve_request(head_only=False)

    def do_HEAD(self) -> None:
        self._serve_request(head_only=True)

    def do_POST(self) -> None:
        path_clean = self.path.split("?", 1)[0].rstrip("/")
        if path_clean == "/api/sessions":
            try:
                self._handle_post_sessions()
            except (BrokenPipeError, ConnectionResetError, TimeoutError, ssl.SSLError):
                self.close_connection = True
            return
        self.send_error(404, "Not Found")

    def end_headers(self) -> None:
        cache_ctl = getattr(self, "_cache_control_override", None) or "no-cache, must-revalidate"
        self._cache_control_override = None
        self.send_header("Cache-Control", cache_ctl)

        has_conn_hdr = any(h.lower().startswith(b"connection:") for h in self._headers_buffer)
        if has_conn_hdr:
            self.close_connection = True
        elif not self.close_connection:
            active_w, max_w = self._executor_utilization()
            pressure_close = max_w >= 16 and active_w >= int(max_w * KEEPALIVE_PRESSURE_RATIO)
            next_count = getattr(self, "_keepalive_requests", 0) + 1
            if pressure_close or next_count >= KEEPALIVE_MAX_REQUESTS:
                self.close_connection = True
                self.send_header("Connection", "close")
            else:
                self.send_header(
                    "Keep-Alive",
                    f"timeout={KEEPALIVE_HEADER_TIMEOUT}, max={KEEPALIVE_MAX_REQUESTS}",
                )

        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        if self.quiet_logs:
            return
        sys.stderr.write(f"[{self.client_address[0]}] {format % args}\n")


def _resolve_process_count(processes: Optional[int] = None) -> int:
    if processes is not None:
        return max(1, int(processes))
    raw = os.environ.get("TARGETS_WORKERS_PROC", "").strip()
    if not raw:
        return 1
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _worker_process_main(
    host: str,
    port: int,
    use_https: bool,
    cert_path_str: Optional[str],
    key_path_str: Optional[str],
    force_gzip: bool,
    quiet_logs: bool,
    max_workers: Optional[int] = None,
    db_path_str: Optional[str] = None,
    redis_url: Optional[str] = None,
    num_shards: Optional[int] = None,
) -> None:
    """Executa um worker HTTP/HTTPS stateless independente compartilhando a mesma porta via SO_REUSEPORT."""
    DevRequestHandler.force_gzip_dir = force_gzip
    DevRequestHandler.quiet_logs = quiet_logs or (os.environ.get("TARGETS_QUIET_LOGS") == "1")
    cache_backend = CacheBackend(redis_url=redis_url) if redis_url is not None else None
    eff_max_workers = _resolve_max_workers(max_workers, default_workers=DEFAULT_CLUSTER_MAX_WORKERS)
    httpd = ThreadedHTTPSServer(
        (host, port),
        DevRequestHandler,
        max_workers=eff_max_workers,
        reuse_port=True,
        db_path=Path(db_path_str) if db_path_str else None,
        cache_backend=cache_backend,
        num_shards=num_shards,
    )
    if use_https and cert_path_str and key_path_str:
        ctx = create_ssl_context(Path(cert_path_str), Path(key_path_str))
        httpd.wrap_ssl(ctx, do_handshake_on_connect=False)

    stop_requested = threading.Event()

    def _on_signal(_signum: int, _frame: Any) -> None:
        stop_requested.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        if cache_backend is not None:
            cache_backend.close()


def iniciar_servidor(
    use_https: bool = True,
    port: Optional[int] = None,
    force_gzip: bool = False,
    quiet_logs: bool = False,
    max_workers: Optional[int] = None,
    processes: Optional[int] = None,
) -> None:
    ip_local = obter_ip_local()
    DevRequestHandler.force_gzip_dir = force_gzip
    DevRequestHandler.quiet_logs = quiet_logs or (os.environ.get("TARGETS_QUIET_LOGS") == "1")
    proc_count = _resolve_process_count(processes)
    default_workers = DEFAULT_CLUSTER_MAX_WORKERS if proc_count > 1 else DEFAULT_MAX_WORKERS
    eff_max_workers = _resolve_max_workers(max_workers, default_workers=default_workers)
    porta = port or (8443 if use_https else 8000)

    cert_path: Optional[Path] = None
    key_path: Optional[Path] = None
    if use_https:
        cert_path, key_path = gerar_certificado_autoassinado(ip_local=ip_local)

    if proc_count > 1:
        mp_ctx = multiprocessing.get_context("spawn")
        workers: list[multiprocessing.Process] = []
        for idx in range(proc_count):
            proc = mp_ctx.Process(
                target=_worker_process_main,
                args=(
                    "0.0.0.0",
                    porta,
                    use_https,
                    str(cert_path) if cert_path else None,
                    str(key_path) if key_path else None,
                    force_gzip,
                    quiet_logs,
                    eff_max_workers,
                ),
                name=f"targets-worker-{idx}",
            )
            proc.start()
            workers.append(proc)

        proto = "HTTPS" if use_https else "HTTP"
        scheme = "https" if use_https else "http"
        print("\n" + "=" * 60)
        print(
            f"CLUSTER {proto} SO_REUSEPORT ATIVO ({proc_count} PROCESSOS, {eff_max_workers} WORKERS/PROC)"
            f"{' [DATA]' if force_gzip else ''}"
        )
        print("=" * 60)
        print(f"• Localhost: {scheme}://localhost:{porta}")
        print(f"• Rede Wi-Fi: {scheme}://{ip_local}:{porta}")
        print(f"• Healthcheck: {scheme}://127.0.0.1:{porta}/api/health")
        print("=" * 60 + "\n")

        try:
            for proc in workers:
                proc.join()
        except KeyboardInterrupt:
            print("\n[!] Encerrando cluster SO_REUSEPORT...")
            for proc in workers:
                if proc.is_alive():
                    proc.terminate()
            for proc in workers:
                proc.join(timeout=5.0)
        return

    if use_https:
        assert cert_path is not None and key_path is not None
        httpd = ThreadedHTTPSServer(("0.0.0.0", porta), DevRequestHandler, max_workers=eff_max_workers, reuse_port=True)
        ctx = create_ssl_context(cert_path, key_path)
        httpd.wrap_ssl(ctx, do_handshake_on_connect=False)

        print("\n" + "=" * 60)
        print(f"SERVIDOR HTTPS CONCORRENTE ATIVO (TARGETS_VIRTU){' [DATA]' if force_gzip else ''}")
        print("=" * 60)
        print(f"• Localhost: https://localhost:{porta}")
        print(f"• Rede Wi-Fi: https://{ip_local}:{porta}")
        print(f"• Healthcheck: https://127.0.0.1:{porta}/api/health")
        print("=" * 60 + "\n")
    else:
        httpd = ThreadedHTTPSServer(("0.0.0.0", porta), DevRequestHandler, max_workers=eff_max_workers, reuse_port=True)
        print("\n" + "=" * 60)
        print(f"SERVIDOR HTTP CONCORRENTE ATIVO (TARGETS_VIRTU){' [DATA]' if force_gzip else ''}")
        print("=" * 60)
        print(f"• Localhost: http://localhost:{porta}")
        print(f"• Rede Wi-Fi: http://{ip_local}:{porta}")
        print(f"• Healthcheck: http://127.0.0.1:{porta}/api/health")
        print("=" * 60 + "\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Servidor encerrado.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    raw_args = sys.argv[1:]
    args = {a.lower() for a in raw_args}
    usar_https = not bool(args & {"http", "--http", "no-ssl"})
    force_gzip = bool(args & {"gzip", "--gzip", "--data"})
    quiet = bool(args & {"quiet", "--quiet", "-q"})
    custom_port: Optional[int] = None
    custom_workers: Optional[int] = None
    custom_processes: Optional[int] = None
    for a in raw_args:
        if a.startswith("--port="):
            custom_port = int(a.split("=", 1)[1])
        elif a.startswith("--workers="):
            custom_workers = int(a.split("=", 1)[1])
        elif a.startswith("--processes=") or a.startswith("--procs="):
            custom_processes = int(a.split("=", 1)[1])
    iniciar_servidor(
        use_https=usar_https,
        port=custom_port,
        force_gzip=force_gzip,
        quiet_logs=quiet,
        max_workers=custom_workers,
        processes=custom_processes,
    )
