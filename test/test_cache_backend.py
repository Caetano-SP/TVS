#!/usr/bin/env python3
"""
Testes da Camada de Cache Híbrida L1 (RAM Zero-Copy) + L2 (Redis com Fallback Transparente)
- Valida CacheBackend (L1 memoryview zero-copy, LRU, TTL, versionamento)
- Valida integração Redis RESP2 via TCP (REDIS_URL / 127.0.0.1:6379)
- Valida fallback transparente em RAM quando o daemon Redis está inativo ou cai
- Valida armazenamento e revalidação de ETag (/api/targets e /api/sessions) com invalidação por sessions:version entre múltiplos workers
"""

import gzip
import http.client
import json
import os
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Optional

os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

BASE_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = BASE_DIR / "edit" / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import servidor_dev  # noqa: E402


class _MockRESP2Server:
    """Servidor TCP RESP2 leve em memória para validar integração real com Redis sem daemon externo."""

    def __init__(self, password: Optional[str] = None) -> None:
        self.password = password
        self.lock = threading.Lock()
        self.kv: dict[str, bytes] = {}
        self.expirations: dict[str, float] = {}
        self.commands: list[str] = []
        self._stop_event = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(32)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        self._clients: list[socket.socket] = []
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        if self.password:
            return f"redis://:{self.password}@127.0.0.1:{self.port}/0"
        return f"redis://127.0.0.1:{self.port}/0"

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                client, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with self.lock:
                self._clients.append(client)
            t = threading.Thread(target=self._handle_client, args=(client,), daemon=True)
            t.start()

    @staticmethod
    def _read_exact(rfile: Any, n: int) -> bytes:
        data = rfile.read(n)
        if data is None or len(data) != n:
            raise ConnectionError("EOF")
        return data

    def _read_command(self, rfile: Any) -> Optional[list[bytes]]:
        line = rfile.readline()
        if not line:
            return None
        if not line.startswith(b"*"):
            raise ValueError(f"Esperado array RESP2, recebido {line!r}")
        count = int(line[1:-2])
        args: list[bytes] = []
        for _ in range(count):
            hdr = rfile.readline()
            if not hdr.startswith(b"$"):
                raise ValueError(f"Esperado bulk string RESP2, recebido {hdr!r}")
            length = int(hdr[1:-2])
            arg = self._read_exact(rfile, length)
            self._read_exact(rfile, 2)
            args.append(arg)
        return args

    def _get_locked(self, key: str) -> Optional[bytes]:
        exp = self.expirations.get(key)
        if exp is not None and time.monotonic() >= exp:
            self.kv.pop(key, None)
            self.expirations.pop(key, None)
            return None
        return self.kv.get(key)

    def _handle_client(self, client: socket.socket) -> None:
        rfile = client.makefile("rb")
        authed = self.password is None
        try:
            while not self._stop_event.is_set():
                args = self._read_command(rfile)
                if args is None:
                    break
                cmd = args[0].decode("ascii", errors="replace").upper()
                with self.lock:
                    self.commands.append(cmd)

                if cmd == "AUTH":
                    pwd = args[-1].decode("utf-8", errors="replace")
                    if self.password is None or pwd == self.password:
                        authed = True
                        client.sendall(b"+OK\r\n")
                    else:
                        client.sendall(b"-WRONGPASS invalid password\r\n")
                    continue

                if not authed:
                    client.sendall(b"-NOAUTH Authentication required.\r\n")
                    continue

                if cmd == "PING":
                    client.sendall(b"+PONG\r\n")
                elif cmd == "SELECT":
                    client.sendall(b"+OK\r\n")
                elif cmd == "GET":
                    key = args[1].decode("utf-8")
                    with self.lock:
                        val = self._get_locked(key)
                    if val is None:
                        client.sendall(b"$-1\r\n")
                    else:
                        client.sendall(f"${len(val)}\r\n".encode("ascii") + val + b"\r\n")
                elif cmd == "SET":
                    key = args[1].decode("utf-8")
                    val = args[2]
                    exp_at: Optional[float] = None
                    if len(args) >= 5:
                        opt = args[3].decode("ascii").upper()
                        ttl_val = float(args[4].decode("ascii"))
                        if opt == "PX":
                            exp_at = time.monotonic() + (ttl_val / 1000.0)
                        elif opt == "EX":
                            exp_at = time.monotonic() + ttl_val
                    with self.lock:
                        self.kv[key] = val
                        if exp_at is not None:
                            self.expirations[key] = exp_at
                        else:
                            self.expirations.pop(key, None)
                    client.sendall(b"+OK\r\n")
                elif cmd == "DEL":
                    removed = 0
                    with self.lock:
                        for raw_k in args[1:]:
                            k = raw_k.decode("utf-8")
                            if k in self.kv:
                                del self.kv[k]
                                self.expirations.pop(k, None)
                                removed += 1
                    client.sendall(f":{removed}\r\n".encode("ascii"))
                elif cmd == "INCRBY":
                    key = args[1].decode("utf-8")
                    delta = int(args[2].decode("ascii"))
                    with self.lock:
                        cur_raw = self._get_locked(key)
                        cur = int(cur_raw.decode("ascii")) if cur_raw is not None else 0
                        new_val = cur + delta
                        self.kv[key] = str(new_val).encode("ascii")
                    client.sendall(f":{new_val}\r\n".encode("ascii"))
                else:
                    client.sendall(f"-ERR unknown command '{cmd}'\r\n".encode("ascii"))
        except Exception:
            pass
        finally:
            try:
                rfile.close()
            except Exception:
                pass
            try:
                client.close()
            except Exception:
                pass

    def close(self) -> None:
        self._stop_event.set()
        try:
            self._sock.close()
        except Exception:
            pass
        with self.lock:
            clients = list(self._clients)
            self._clients.clear()
        for c in clients:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                c.close()
            except Exception:
                pass
        self._thread.join(timeout=1.0)


def _get_unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class TestHybridCacheBackend(unittest.TestCase):
    def test_l1_zero_copy_lru_ttl_and_transparent_redis_fallback(self):
        """Valida L1 RAM zero-copy (memoryview), LRU, TTL e fallback transparente sem Redis ativo."""
        dead_port = _get_unused_tcp_port()
        cache = servidor_dev.CacheBackend(
            redis_url=f"redis://127.0.0.1:{dead_port}/0",
            l1_max_bytes=64,
            l1_max_entries=4,
            connect_timeout=0.05,
            retry_interval=5.0,
        )
        try:
            self.assertFalse(cache.ping_l2())
            self.assertFalse(cache.is_l2_active())

            # Grava e lê em L1 como memoryview zero-copy mesmo com Redis offline
            payload = b"0123456789abcdef" * 2  # 32 bytes
            cache.set("k1", payload)
            view = cache.get_view("k1")
            self.assertIsInstance(view, memoryview)
            self.assertEqual(bytes(view), payload)

            # Evicção LRU por tamanho (32 + 32 + 32 > 64)
            cache.set("k2", b"b" * 32)
            cache.set("k3", b"c" * 32)
            self.assertIsNone(cache.get("k1"))
            self.assertEqual(cache.get("k2"), b"b" * 32)
            self.assertEqual(cache.get("k3"), b"c" * 32)

            # Expiração por TTL em L1
            cache.set("short", b"temp", ttl=0.05)
            self.assertEqual(cache.get("short"), b"temp")
            time.sleep(0.07)
            self.assertIsNone(cache.get("short"))

            # Versionamento local em fallback RAM
            self.assertEqual(cache.get_version("sessions:version"), 0)
            self.assertEqual(cache.incr_version("sessions:version"), 1)
            self.assertEqual(cache.incr_version("sessions:version"), 2)
            self.assertEqual(cache.get_version("sessions:version"), 2)

            stats = cache.stats()
            self.assertGreaterEqual(stats["l1_hits"], 3)
            self.assertGreaterEqual(stats["l2_fallbacks"], 1)
        finally:
            cache.close()

    def test_redis_url_env_l2_sync_and_failover(self):
        """Valida integração L2 via REDIS_URL (incluindo AUTH) e failover transparente se Redis cair."""
        redis_srv = _MockRESP2Server(password="virtu-secret")
        prev_env = os.environ.get("REDIS_URL")
        os.environ["REDIS_URL"] = redis_srv.url
        try:
            worker1 = servidor_dev.CacheBackend(connect_timeout=0.5, retry_interval=5.0)
            worker2 = servidor_dev.CacheBackend(connect_timeout=0.5, retry_interval=5.0)
            try:
                self.assertTrue(worker1.ping_l2())
                self.assertTrue(worker2.ping_l2())

                worker1.set("shared:etag", b'"abc-123"', ttl=30.0)
                # Worker 2 tem L1 frio, deve buscar do L2 Redis e promover para sua L1
                self.assertEqual(worker2.get("shared:etag"), b'"abc-123"')
                self.assertEqual(worker2.stats()["l2_hits"], 1)
                # Segunda leitura no Worker 2 deve bater em L1 RAM
                self.assertEqual(bytes(worker2.get_view("shared:etag")), b'"abc-123"')
                self.assertGreaterEqual(worker2.stats()["l1_hits"], 1)

                # Simula queda do daemon Redis em tempo de execução
                redis_srv.close()
                # Leitura já em L1 continua sub-milissegundo; escrita cai para L1 sem exceção
                worker2.set("post_outage", b"ok-ram")
                self.assertEqual(worker2.get("post_outage"), b"ok-ram")
                self.assertFalse(worker2.is_l2_active())
            finally:
                worker1.close()
                worker2.close()
        finally:
            redis_srv.close()
            if prev_env is None:
                os.environ.pop("REDIS_URL", None)
            else:
                os.environ["REDIS_URL"] = prev_env

    def test_multiworker_etag_and_sessions_version_invalidation_with_redis(self):
        """
        Valida cenário multi-worker com Redis L2 compartilhado:
        - /api/targets armazena bundle e ETags no CacheBackend e revalida 304 entre workers
        - /api/sessions usa sessions:version para invalidar cache/ETag entre Worker 1 (escrita) e Worker 2 (leitura)
        """
        redis_srv = _MockRESP2Server()
        cert_path, key_path = servidor_dev.gerar_certificado_autoassinado()
        ctx1 = servidor_dev.create_ssl_context(cert_path, key_path)
        ctx2 = servidor_dev.create_ssl_context(cert_path, key_path)
        client_ctx = ssl._create_unverified_context()

        prev_quiet = servidor_dev.DevRequestHandler.quiet_logs
        prev_gzip = servidor_dev.DevRequestHandler.force_gzip_dir
        servidor_dev.DevRequestHandler.quiet_logs = True
        servidor_dev.DevRequestHandler.force_gzip_dir = True

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_db = Path(tmp_dir) / "cluster_sessions.db"
            cache1 = servidor_dev.CacheBackend(redis_url=redis_srv.url, connect_timeout=0.5, retry_interval=5.0)
            cache2 = servidor_dev.CacheBackend(redis_url=redis_srv.url, connect_timeout=0.5, retry_interval=5.0)

            httpd1 = servidor_dev.ThreadedHTTPSServer(
                ("127.0.0.1", 0),
                servidor_dev.DevRequestHandler,
                max_workers=8,
                db_path=tmp_db,
                cache_backend=cache1,
            )
            httpd2 = servidor_dev.ThreadedHTTPSServer(
                ("127.0.0.1", 0),
                servidor_dev.DevRequestHandler,
                max_workers=8,
                db_path=tmp_db,
                cache_backend=cache2,
            )
            port1 = httpd1.server_address[1]
            port2 = httpd2.server_address[1]
            httpd1.socket = ctx1.wrap_socket(httpd1.socket, server_side=True)
            httpd2.socket = ctx2.wrap_socket(httpd2.socket, server_side=True)

            t1 = threading.Thread(target=httpd1.serve_forever, daemon=True)
            t2 = threading.Thread(target=httpd2.serve_forever, daemon=True)
            t1.start()
            t2.start()

            try:
                c1 = http.client.HTTPSConnection("127.0.0.1", port1, context=client_ctx, timeout=5)
                c2 = http.client.HTTPSConnection("127.0.0.1", port2, context=client_ctx, timeout=5)
                try:
                    # 1. /api/targets populado no Worker 1 -> ETag armazenado no Redis -> 304 no Worker 2
                    c1.request("GET", "/api/targets", headers={"Accept-Encoding": "gzip"})
                    r_t1 = c1.getresponse()
                    body_t1 = gzip.decompress(r_t1.read())
                    self.assertEqual(r_t1.status, 200)
                    etag_targets = r_t1.getheader("ETag")
                    self.assertTrue(etag_targets)
                    self.assertGreater(len(json.loads(body_t1.decode("utf-8"))), 0)

                    with redis_srv.lock:
                        self.assertIn("targets_virtu:targets:etag:plain", redis_srv.kv)
                        self.assertIn("targets_virtu:targets:etag:gzip", redis_srv.kv)

                    c2.request("GET", "/api/targets", headers={"Accept-Encoding": "gzip", "If-None-Match": etag_targets})
                    r_t2 = c2.getresponse()
                    self.assertEqual(len(r_t2.read()), 0)
                    self.assertEqual(r_t2.status, 304)

                    # 2. /api/sessions no Worker 2 inicialmente vazio -> ETag + 304 Not Modified
                    c2.request("GET", "/api/sessions?limit=10", headers={"Accept-Encoding": "gzip"})
                    r_s0 = c2.getresponse()
                    s0_data = json.loads(r_s0.read().decode("utf-8"))
                    self.assertEqual(r_s0.status, 200)
                    self.assertEqual(s0_data["count"], 0)
                    etag_s0 = r_s0.getheader("ETag")
                    self.assertTrue(etag_s0)
                    cc_s0 = (r_s0.getheader("Cache-Control") or "").lower()
                    self.assertIn("no-cache", cc_s0)
                    self.assertNotIn("no-store", cc_s0)

                    c2.request("GET", "/api/sessions?limit=10", headers={"If-None-Match": etag_s0})
                    r_s0_304 = c2.getresponse()
                    self.assertEqual(len(r_s0_304.read()), 0)
                    self.assertEqual(r_s0_304.status, 304)

                    # 3. Worker 1 recebe POST /api/sessions -> commit incrementa sessions:version no Redis
                    post_body = json.dumps({
                        "session_id": "cluster-sess-1",
                        "target": "VIRTU_A4",
                        "shots": [{"id": 1, "x": 250, "y": 310}],
                    }).encode("utf-8")
                    c1.request(
                        "POST",
                        "/api/sessions",
                        body=post_body,
                        headers={"Content-Type": "application/json", "Content-Length": str(len(post_body))},
                    )
                    r_post = c1.getresponse()
                    r_post.read()
                    self.assertEqual(r_post.status, 202)

                    # Aguarda worker SQLite do Worker 1 commitar e incrementar sessions:version no Redis
                    deadline = time.monotonic() + 3.0
                    while time.monotonic() < deadline:
                        if cache2.get_version("sessions:version") >= 1:
                            break
                        time.sleep(0.02)
                    self.assertGreaterEqual(cache2.get_version("sessions:version"), 1)

                    # 4. Worker 2 com o ETag antigo (etag_s0) deve invalidar via sessions:version e retornar 200 OK
                    c2.request("GET", "/api/sessions?limit=10", headers={"If-None-Match": etag_s0})
                    r_s1 = c2.getresponse()
                    s1_data = json.loads(r_s1.read().decode("utf-8"))
                    self.assertEqual(r_s1.status, 200)
                    self.assertEqual(s1_data["count"], 1)
                    self.assertEqual(s1_data["sessions"][0]["session_id"], "cluster-sess-1")
                    etag_s1 = r_s1.getheader("ETag")
                    self.assertNotEqual(etag_s1, etag_s0)

                    # 5. Revalidação condicional com novo ETag no Worker 1 (lendo meta de L2 Redis!) retorna 304
                    c1.request("GET", "/api/sessions?limit=10", headers={"If-None-Match": etag_s1})
                    r_s1_304 = c1.getresponse()
                    self.assertEqual(len(r_s1_304.read()), 0)
                    self.assertEqual(r_s1_304.status, 304)
                finally:
                    c1.close()
                    c2.close()
            finally:
                servidor_dev.DevRequestHandler.quiet_logs = prev_quiet
                servidor_dev.DevRequestHandler.force_gzip_dir = prev_gzip
                httpd1.shutdown()
                httpd1.server_close()
                httpd2.shutdown()
                httpd2.server_close()
                cache1.close()
                cache2.close()
                redis_srv.close()

    def test_http_server_fallback_when_redis_daemon_inactive(self):
        """Garante resposta normal e 304 ETag em /api/sessions e /api/targets sem daemon Redis ativo."""
        dead_port = _get_unused_tcp_port()
        cert_path, key_path = servidor_dev.gerar_certificado_autoassinado()
        ctx = servidor_dev.create_ssl_context(cert_path, key_path)
        client_ctx = ssl._create_unverified_context()

        prev_quiet = servidor_dev.DevRequestHandler.quiet_logs
        servidor_dev.DevRequestHandler.quiet_logs = True
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_db = Path(tmp_dir) / "fallback_sessions.db"
            cache = servidor_dev.CacheBackend(
                redis_url=f"redis://127.0.0.1:{dead_port}/0",
                connect_timeout=0.05,
                retry_interval=10.0,
            )
            httpd = servidor_dev.ThreadedHTTPSServer(
                ("127.0.0.1", 0),
                servidor_dev.DevRequestHandler,
                max_workers=8,
                db_path=tmp_db,
                cache_backend=cache,
            )
            port = httpd.server_address[1]
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                conn = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=5)
                try:
                    conn.request("GET", "/api/targets")
                    rt = conn.getresponse()
                    rt.read()
                    self.assertEqual(rt.status, 200)
                    etag_t = rt.getheader("ETag")

                    conn.request("GET", "/api/targets", headers={"If-None-Match": etag_t})
                    rt304 = conn.getresponse()
                    rt304.read()
                    self.assertEqual(rt304.status, 304)

                    conn.request("GET", "/api/sessions?limit=5")
                    rs = conn.getresponse()
                    self.assertEqual(rs.status, 200)
                    rs_json = json.loads(rs.read().decode("utf-8"))
                    self.assertEqual(rs_json["count"], 0)
                    etag_s = rs.getheader("ETag")

                    conn.request("GET", "/api/sessions?limit=5", headers={"If-None-Match": etag_s})
                    rs304 = conn.getresponse()
                    rs304.read()
                    self.assertEqual(rs304.status, 304)
                finally:
                    conn.close()
            finally:
                servidor_dev.DevRequestHandler.quiet_logs = prev_quiet
                httpd.shutdown()
                httpd.server_close()
                cache.close()


if __name__ == "__main__":
    unittest.main()
