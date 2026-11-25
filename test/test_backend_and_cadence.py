#!/usr/bin/env python3
"""
Testes de Backend e Motor de Cadência Rápida (TARGETS_VIRTU)
- Valida servidor_dev.py (ThreadingHTTPServer, HTTPS com SAN, gzip, proteção path traversal)
- Valida laser-worker.js via Node.js (cadência rápida 150ms-220ms, anti-duplicação de pulso único e ping-pong buffer)
"""

import gzip
import http.client
import json
import os
import sqlite3

os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = BASE_DIR / "edit" / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import servidor_dev  # noqa: E402


class TestBackendAndCadence(unittest.TestCase):
    def test_san_certificate_and_concurrent_https_gzip(self):
        cert_path, key_path = servidor_dev.gerar_certificado_autoassinado()
        self.assertTrue(cert_path.exists())
        self.assertTrue(key_path.exists())

        out = subprocess.check_output(
            ["openssl", "x509", "-in", str(cert_path), "-noout", "-text"],
            text=True,
        )
        self.assertIn("Subject Alternative Name", out)
        self.assertIn("127.0.0.1", out)
        self.assertIn("localhost", out)

        servidor_dev.DevRequestHandler.force_gzip_dir = True
        httpd = servidor_dev.ThreadedHTTPSServer(("127.0.0.1", 0), servidor_dev.DevRequestHandler)
        port = httpd.server_address[1]
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)

        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            client_ctx = ssl._create_unverified_context()

            def fetch(path):
                req = urllib.request.Request(
                    f"https://127.0.0.1:{port}{path}",
                    headers={"Accept-Encoding": "gzip"},
                )
                with urllib.request.urlopen(req, context=client_ctx, timeout=5) as resp:
                    body = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip":
                        body = gzip.decompress(body)
                    return resp.status, resp.headers.get("Content-Encoding"), body

            paths = ["/index.html", "/app.js", "/style.css", "/api/targets"] * 3
            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(fetch, paths))

            for status, enc, body in results:
                self.assertEqual(status, 200)
                self.assertEqual(enc, "gzip")
                self.assertGreater(len(body), 50)

            # Bloqueio de path traversal
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(
                    f"https://127.0.0.1:{port}/../../requirements.txt",
                    context=client_ctx,
                    timeout=5,
                )
            self.assertIn(cm.exception.code, (403, 404))
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_qr_removed_tls_optimized_cache_control_and_brotli_priority(self):
        self.assertFalse(hasattr(servidor_dev, "atualizar_qrcode_conexao"))
        self.assertFalse((BASE_DIR / "edit" / "src" / "qrcode_conexao.png").exists())
        self.assertFalse((BASE_DIR / "data" / "qrcode_conexao.png.gz").exists())
        for mod_name in ("qrcode", "cv2", "numpy", "PIL"):
            self.assertNotIn(mod_name, vars(servidor_dev))

        cert_path, key_path = servidor_dev.gerar_certificado_autoassinado()
        ctx = servidor_dev.create_ssl_context(cert_path, key_path)
        self.assertTrue(bool(ctx.options & ssl.OP_NO_COMPRESSION))
        if hasattr(ssl, "OP_NO_TICKET"):
            self.assertFalse(bool(ctx.options & ssl.OP_NO_TICKET))

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_db = Path(tmp_dir) / "sessions.db"
            servidor_dev.DevRequestHandler.force_gzip_dir = True
            httpd = servidor_dev.ThreadedHTTPSServer(
                ("127.0.0.1", 0),
                servidor_dev.DevRequestHandler,
                max_workers=8,
                db_path=tmp_db,
            )
            port = httpd.server_address[1]
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)

            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()

            fake_br_path = BASE_DIR / "data" / "style.css.br"
            fake_br_payload = b"BROTLI_TEST_PAYLOAD"
            fake_br_created = False
            try:
                client_ctx = ssl._create_unverified_context()
                conn = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=5)
                try:
                    # qrcode_conexao.png deve retornar 404
                    conn.request("GET", "/qrcode_conexao.png")
                    r_qr = conn.getresponse()
                    r_qr.read()
                    self.assertEqual(r_qr.status, 404)

                    # Imagens estaticas de alvos devem usar Cache-Control immutable
                    conn.request("GET", "/api/targets")
                    r_targets = conn.getresponse()
                    targets_list = json.loads(r_targets.read().decode("utf-8"))
                    self.assertGreater(len(targets_list), 0)

                    img_url = targets_list[0]["url"]
                    conn.request("GET", img_url)
                    r_img = conn.getresponse()
                    img_body = r_img.read()
                    self.assertEqual(r_img.status, 200)
                    self.assertGreater(len(img_body), 100)
                    img_cc = (r_img.getheader("Cache-Control") or "").lower()
                    self.assertIn("max-age=86400", img_cc)
                    self.assertIn("immutable", img_cc)

                    # Prioridade de .br sobre .gz quando Accept-Encoding inclui br
                    if not fake_br_path.exists():
                        fake_br_path.write_bytes(fake_br_payload)
                        fake_br_created = True
                    conn.request("GET", "/style.css", headers={"Accept-Encoding": "gzip, br"})
                    r_br = conn.getresponse()
                    br_body = r_br.read()
                    self.assertEqual(r_br.status, 200)
                    self.assertEqual(r_br.getheader("Content-Encoding"), "br")
                    if fake_br_created:
                        self.assertEqual(br_body, fake_br_payload)
                finally:
                    conn.close()
            finally:
                if fake_br_created and fake_br_path.exists():
                    fake_br_path.unlink()
                httpd.shutdown()
                httpd.server_close()

    def test_sessions_async_persistence_backpressure_and_graceful_drain(self):
        cert_path, key_path = servidor_dev.gerar_certificado_autoassinado()
        ctx = servidor_dev.create_ssl_context(cert_path, key_path)
        client_ctx = ssl._create_unverified_context()

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_db = Path(tmp_dir) / "sessions.db"
            httpd = servidor_dev.ThreadedHTTPSServer(
                ("127.0.0.1", 0),
                servidor_dev.DevRequestHandler,
                max_workers=8,
                db_path=tmp_db,
            )
            port = httpd.server_address[1]
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()

            try:
                conn = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=5)
                try:
                    # 1. POST valido -> 202 Accepted imediato
                    payload = {
                        "session_id": "sess-001",
                        "target": "VIRTU_A4",
                        "shots": [{"id": 1, "x": 300, "y": 400, "split": "0.21"}],
                    }
                    raw = json.dumps(payload).encode("utf-8")
                    conn.request(
                        "POST",
                        "/api/sessions",
                        body=raw,
                        headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
                    )
                    r_post = conn.getresponse()
                    post_res = json.loads(r_post.read().decode("utf-8"))
                    self.assertEqual(r_post.status, 202)
                    self.assertEqual(post_res["status"], "accepted")
                    self.assertEqual(post_res["session_id"], "sess-001")

                    # 2. GET /api/sessions?limit=10 -> aguarda worker gravar e valida retorno
                    found = []
                    for _ in range(30):
                        conn.request("GET", "/api/sessions?limit=10")
                        r_get = conn.getresponse()
                        self.assertEqual(r_get.status, 200)
                        data = json.loads(r_get.read().decode("utf-8"))
                        if data["count"] >= 1:
                            found = data["sessions"]
                            break
                        time.sleep(0.02)
                    self.assertEqual(len(found), 1)
                    self.assertEqual(found[0]["session_id"], "sess-001")
                    self.assertEqual(found[0]["target"], "VIRTU_A4")
                    self.assertEqual(found[0]["shot_count"], 1)
                    self.assertEqual(found[0]["payload"]["shots"][0]["x"], 300)

                    # 3. JSON invalido -> 400 Bad Request
                    bad_json = b"{not-valid-json"
                    conn.request(
                        "POST",
                        "/api/sessions",
                        body=bad_json,
                        headers={"Content-Type": "application/json", "Content-Length": str(len(bad_json))},
                    )
                    r_400 = conn.getresponse()
                    r_400.read()
                    self.assertEqual(r_400.status, 400)

                    # 4. Payload acima de 64KB -> 413 Payload Too Large antes da leitura
                    conn.putrequest("POST", "/api/sessions")
                    conn.putheader("Content-Type", "application/json")
                    conn.putheader("Content-Length", str(servidor_dev.MAX_SESSION_PAYLOAD_BYTES + 1))
                    conn.endheaders()
                    r_413 = conn.getresponse()
                    r_413.read()
                    self.assertEqual(r_413.status, 413)
                finally:
                    conn.close()

                # 5. Backpressure (503 Service Unavailable quando queue.Full)
                conn_bp = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=5)
                orig_put_nowait = httpd.session_store._queue.put_nowait
                try:
                    import queue as _q

                    def raise_full(_item):
                        raise _q.Full()

                    httpd.session_store._queue.put_nowait = raise_full
                    bp_body = json.dumps({"session_id": "sess-bp", "shots": []}).encode("utf-8")
                    conn_bp.request(
                        "POST",
                        "/api/sessions",
                        body=bp_body,
                        headers={"Content-Type": "application/json", "Content-Length": str(len(bp_body))},
                    )
                    r_503 = conn_bp.getresponse()
                    r_503.read()
                    self.assertEqual(r_503.status, 503)
                finally:
                    httpd.session_store._queue.put_nowait = orig_put_nowait
                    conn_bp.close()

                # 6. Enfileira lote e valida graceful drain via sentinela no server_close()
                conn_drain = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=5)
                try:
                    for idx in range(5):
                        item = json.dumps({"session_id": f"drain-{idx}", "shots": []}).encode("utf-8")
                        conn_drain.request(
                            "POST",
                            "/api/sessions",
                            body=item,
                            headers={"Content-Type": "application/json", "Content-Length": str(len(item))},
                        )
                        r_ok = conn_drain.getresponse()
                        r_ok.read()
                        self.assertEqual(r_ok.status, 202)
                finally:
                    conn_drain.close()
            finally:
                httpd.shutdown()
                httpd.server_close()

            # Verifica diretamente no SQLite WAL apos server_close() que todas as 6 sessoes foram gravadas
            db_conn = sqlite3.connect(str(tmp_db))
            try:
                mode = db_conn.execute("PRAGMA journal_mode;").fetchone()[0]
                total = db_conn.execute("SELECT COUNT(*) FROM sessions;").fetchone()[0]
                self.assertEqual(str(mode).lower(), "wal")
                self.assertEqual(total, 6)
            finally:
                db_conn.close()

    def test_session_store_wal_pragmas_queue_and_sliding_retention(self):
        """Valida fila 16384, lotes 512, PRAGMAs de endurance WAL/mmap/temp_store e poda em janela deslizante."""
        self.assertEqual(servidor_dev.SESSION_QUEUE_MAXSIZE, 16384)
        self.assertEqual(servidor_dev.SESSION_BATCH_SIZE, 512)
        self.assertEqual(servidor_dev.MAX_STORED_SESSIONS, 10000)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_db = Path(tmp_dir) / "endurance.db"
            conn = sqlite3.connect(str(tmp_db))
            try:
                servidor_dev.SessionStore._configure_connection(conn)
                mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
                autockpt = conn.execute("PRAGMA wal_autocheckpoint;").fetchone()[0]
                jsize = conn.execute("PRAGMA journal_size_limit;").fetchone()[0]
                mmap_sz = conn.execute("PRAGMA mmap_size;").fetchone()[0]
                temp_st = conn.execute("PRAGMA temp_store;").fetchone()[0]
                self.assertEqual(str(mode).lower(), "wal")
                self.assertEqual(int(autockpt), 1000)
                self.assertEqual(int(jsize), 67108864)
                self.assertEqual(int(mmap_sz), 268435456)
                self.assertEqual(int(temp_st), 2)  # 2 = MEMORY
            finally:
                conn.close()

            store = servidor_dev.SessionStore(
                db_path=tmp_db,
                maxsize=servidor_dev.SESSION_QUEUE_MAXSIZE,
                max_stored_sessions=25,
                prune_every_commits=2,
            )
            self.assertEqual(store._queue.maxsize, 16384)
            for idx in range(80):
                store.enqueue({"session_id": f"end-{idx:03d}", "target": "VIRTU_A4", "shots": []})
            store.close()

            check_conn = sqlite3.connect(str(tmp_db))
            try:
                indexes = {
                    r[0]
                    for r in check_conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='sessions';"
                    ).fetchall()
                }
                self.assertIn("idx_sessions_id_desc", indexes)
                self.assertIn("idx_sessions_target_id_desc", indexes)
                self.assertIn("idx_sessions_session_id", indexes)
                self.assertIn("idx_sessions_created_at", indexes)

                plan_target = " ".join(
                    str(r)
                    for r in check_conn.execute(
                        "EXPLAIN QUERY PLAN SELECT id, session_id FROM sessions WHERE target='VIRTU_A4' ORDER BY id DESC LIMIT 10;"
                    ).fetchall()
                )
                self.assertIn("idx_sessions_target_id_desc", plan_target)

                rows = check_conn.execute(
                    "SELECT session_id FROM sessions ORDER BY id ASC;"
                ).fetchall()
                ids = [r[0] for r in rows]
                self.assertEqual(len(ids), 25)
                self.assertEqual(ids[0], "end-055")
                self.assertEqual(ids[-1], "end-079")
            finally:
                check_conn.close()

    def test_session_store_sharding_high_throughput_and_http_query(self):
        """Valida particionamento via TARGETS_DB_SHARDS (sessions_shard_N.db), lotes 512 e consulta HTTP."""
        prev_shards = os.environ.get("TARGETS_DB_SHARDS")
        os.environ["TARGETS_DB_SHARDS"] = "4"
        cert_path, key_path = servidor_dev.gerar_certificado_autoassinado()
        ctx = servidor_dev.create_ssl_context(cert_path, key_path)
        client_ctx = ssl._create_unverified_context()
        prev_quiet = servidor_dev.DevRequestHandler.quiet_logs
        servidor_dev.DevRequestHandler.quiet_logs = True

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                base_db = Path(tmp_dir) / "sessions.db"
                store = servidor_dev.SessionStore(db_path=base_db)
                self.assertEqual(store.num_shards, 4)
                self.assertEqual(
                    [p.name for p in store.shard_paths],
                    [
                        "sessions_shard_0.db",
                        "sessions_shard_1.db",
                        "sessions_shard_2.db",
                        "sessions_shard_3.db",
                    ],
                )
                self.assertGreaterEqual(store.queue_capacity(), 16384)

                targets = ("VIRTU_A4", "IPSC", "IDPA", "B27")
                total_items = 600
                for idx in range(total_items):
                    store.enqueue(
                        {
                            "session_id": f"sh-{idx:04d}",
                            "target": targets[idx % len(targets)],
                            "created_at": f"2026-11-23T15:00:{idx // 60:02d}.{idx % 60:06d}+00:00",
                            "shots": [{"id": 1, "x": 100, "y": 200}],
                        }
                    )
                store.close()

                # Verifica distribuição real entre os 4 shards e integridade total de 600 registros
                counts_per_shard = []
                for sp in store.shard_paths:
                    self.assertTrue(sp.exists(), f"Shard não criado: {sp}")
                    c = sqlite3.connect(str(sp))
                    try:
                        cnt = c.execute("SELECT COUNT(*) FROM sessions;").fetchone()[0]
                        counts_per_shard.append(cnt)
                    finally:
                        c.close()

                self.assertEqual(sum(counts_per_shard), total_items)
                self.assertTrue(all(cnt > 0 for cnt in counts_per_shard), f"Distribuição desigual: {counts_per_shard}")

                # Reabre via ThreadedHTTPSServer usando TARGETS_DB_SHARDS=4 e valida GET /api/sessions com e sem target
                httpd = servidor_dev.ThreadedHTTPSServer(
                    ("127.0.0.1", 0),
                    servidor_dev.DevRequestHandler,
                    max_workers=8,
                    db_path=base_db,
                )
                port = httpd.server_address[1]
                httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
                t = threading.Thread(target=httpd.serve_forever, daemon=True)
                t.start()
                try:
                    conn = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=5)
                    try:
                        conn.request("GET", "/api/sessions?limit=15")
                        r_all = conn.getresponse()
                        d_all = json.loads(r_all.read().decode("utf-8"))
                        self.assertEqual(r_all.status, 200)
                        self.assertEqual(d_all["count"], 15)
                        self.assertEqual(d_all["sessions"][0]["session_id"], "sh-0599")

                        conn.request("GET", "/api/sessions?limit=10&target=IPSC")
                        r_ipsc = conn.getresponse()
                        d_ipsc = json.loads(r_ipsc.read().decode("utf-8"))
                        self.assertEqual(r_ipsc.status, 200)
                        self.assertEqual(d_ipsc["count"], 10)
                        self.assertTrue(all(s["target"] == "IPSC" for s in d_ipsc["sessions"]))
                    finally:
                        conn.close()
                finally:
                    httpd.shutdown()
                    httpd.server_close()
        finally:
            servidor_dev.DevRequestHandler.quiet_logs = prev_quiet
            if prev_shards is None:
                os.environ.pop("TARGETS_DB_SHARDS", None)
            else:
                os.environ["TARGETS_DB_SHARDS"] = prev_shards

    def test_laser_worker_rapid_cadence_150ms_to_220ms(self):
        worker_path = BASE_DIR / "edit" / "src" / "workers" / "laser-worker.js"
        node_script = r"""
const fs = require("fs");
const vm = require("vm");
const code = fs.readFileSync(process.argv[1], "utf8");

const outputs = [];
const sandbox = {
  self: {
    postMessage(msg, transferList) {
      outputs.push({ msg, hasTransfer: Array.isArray(transferList) && transferList.length === 1 });
    }
  },
  Float32Array,
  Uint8ClampedArray,
  ArrayBuffer,
  Math,
  Number
};
vm.runInNewContext(code, sandbox);

const W = 240, H = 320;
function makeFrame(spot = null) {
  const buf = new ArrayBuffer(W * H * 4);
  const u8 = new Uint8ClampedArray(buf);
  for (let i = 0; i < u8.length; i += 4) {
    u8[i] = 200; u8[i + 1] = 200; u8[i + 2] = 200; u8[i + 3] = 255;
  }
  if (spot) {
    for (let dy = -3; dy <= 3; dy++) {
      for (let dx = -3; dx <= 3; dx++) {
        const px = spot.x + dx, py = spot.y + dy;
        const idx = (py * W + px) * 4;
        u8[idx] = 255; u8[idx + 1] = 80; u8[idx + 2] = 80;
      }
    }
  }
  return buf;
}

function step(spot, now, lastShotTimestamp = 0) {
  const buf = makeFrame(spot);
  sandbox.self.onmessage({
    data: {
      type: "detect",
      payload: {
        buffer: buf,
        startX: 0, endX: W, startY: 0, endY: H,
        threshold: 210, purity: 38,
        lastShotTimestamp, cooldownMs: 95, now
      }
    }
  });
  return outputs[outputs.length - 1];
}

// Frame base de fundo
const bgRes = step(null, 100);

// 1) Tiro #1 longo (80ms) em (100,100): frames em 200ms e 250ms -> deve contar APENAS 1 tiro
const s1a = step({ x: 100, y: 100 }, 200, 0);
const s1b = step({ x: 100, y: 100 }, 250, 200);
// Apagou entre tiros
const gap1 = step(null, 290, 200);

// 2) Tiro #2 apos 150ms no MESMO pixel (t=350ms, dt=150ms) -> DEVE detectar!
const s2 = step({ x: 100, y: 100 }, 350, 200);
const gap2 = step(null, 420, 350);

// 3) Tiro #3 apos 220ms (t=570ms, dt=220ms) -> DEVE detectar!
const s3 = step({ x: 100, y: 100 }, 570, 350);

// 4) Tiro #4 em VGA 15 FPS: 20px distante (120,100) apos 155ms SEM frame escuro intermediario -> DEVE detectar!
const s4 = step({ x: 120, y: 100 }, 725, 570);

console.log(JSON.stringify({
  bgTransfer: bgRes.hasTransfer,
  s1a: s1a.msg.payload.hit,
  s1b: s1b.msg.payload.hit,
  gap1: gap1.msg.payload.hit,
  s2: s2.msg.payload.hit,
  s3: s3.msg.payload.hit,
  s4: s4.msg.payload.hit
}));
"""
        raw = subprocess.check_output(
            ["node", "-e", node_script, str(worker_path)],
            text=True,
        ).strip()
        res = json.loads(raw)
        self.assertTrue(res["bgTransfer"])
        self.assertTrue(res["s1a"])
        self.assertFalse(res["s1b"])
        self.assertFalse(res["gap1"])
        self.assertTrue(res["s2"], "Falhou ao detectar cadencia rapida de 150ms no mesmo ponto")
        self.assertTrue(res["s3"], "Falhou ao detectar cadencia de 220ms")
        self.assertTrue(res["s4"], "Falhou ao detectar tiro rapido de 155ms em ponto distinto a ~14 FPS")

    def test_laser_worker_false_positive_rejection(self):
        worker_path = BASE_DIR / "edit" / "src" / "workers" / "laser-worker.js"
        node_script = r"""
const fs = require("fs");
const vm = require("vm");
const code = fs.readFileSync(process.argv[1], "utf8");

const outputs = [];
const sandbox = {
  self: {
    postMessage(msg, transferList) {
      outputs.push(msg);
    }
  },
  Float32Array,
  Uint8ClampedArray,
  ArrayBuffer,
  Math,
  Number
};
vm.runInNewContext(code, sandbox);

const W = 240, H = 320;
function makeBase(r = 180, g = 180, b = 180) {
  const buf = new ArrayBuffer(W * H * 4);
  const u8 = new Uint8ClampedArray(buf);
  for (let i = 0; i < u8.length; i += 4) {
    u8[i] = r; u8[i + 1] = g; u8[i + 2] = b; u8[i + 3] = 255;
  }
  return { buf, u8 };
}

function paintRect(u8, cx, cy, half, r, g, b) {
  for (let dy = -half; dy <= half; dy++) {
    for (let dx = -half; dx <= half; dx++) {
      const idx = ((cy + dy) * W + (cx + dx)) * 4;
      u8[idx] = r; u8[idx + 1] = g; u8[idx + 2] = b;
    }
  }
}

function send(buf, now, lastShotTimestamp = 0) {
  sandbox.self.onmessage({
    data: {
      type: "detect",
      payload: {
        buffer: buf,
        startX: 0, endX: W, startY: 0, endY: H,
        threshold: 215, purity: 42,
        lastShotTimestamp, cooldownMs: 95, now
      }
    }
  });
  return outputs[outputs.length - 1].payload.hit;
}

// Frame base
send(makeBase(180, 180, 180).buf, 100);

// 1) Hot pixel isolado (1 pixel em grade step=2)
const fHot = makeBase(180, 180, 180);
paintRect(fHot.u8, 60, 60, 0, 255, 20, 20);
const hotPixelHit = send(fHot.buf, 200);

// 2) Flicker de lampada quente / papel claro (sem dominancia vermelha real)
const fFlicker = makeBase(180, 180, 180);
paintRect(fFlicker.u8, 120, 120, 3, 244, 234, 228);
const flickerHit = send(fFlicker.buf, 300);

// 3) Vibracao de tripe / artefatos em pontos distantes da ROI (span > maxSpotSpan)
const fShake = makeBase(180, 180, 180);
paintRect(fShake.u8, 20, 20, 2, 255, 50, 50);
paintRect(fShake.u8, 190, 240, 2, 255, 50, 50);
const shakeHit = send(fShake.buf, 400);

// 4) Tiro real seguido de arrasto de gatilho (gatilhaco) em 90ms (22px distante)
const fShot = makeBase(180, 180, 180);
paintRect(fShot.u8, 100, 100, 3, 255, 60, 60);
const realShotHit = send(fShot.buf, 600, 0);

const fDrag = makeBase(180, 180, 180);
paintRect(fDrag.u8, 122, 100, 3, 255, 60, 60);
const dragDoubleHit = send(fDrag.buf, 690, 600);

console.log(JSON.stringify({
  hotPixelHit,
  flickerHit,
  shakeHit,
  realShotHit,
  dragDoubleHit
}));
"""
        raw = subprocess.check_output(
            ["node", "-e", node_script, str(worker_path)],
            text=True,
        ).strip()
        res = json.loads(raw)
        self.assertFalse(res["hotPixelHit"], "Hot pixel isolado nao deve gerar disparo")
        self.assertFalse(res["flickerHit"], "Flicker de luz quente/papel branco nao deve gerar disparo")
        self.assertFalse(res["shakeHit"], "Vibracao com pontos dispersos na ROI nao deve gerar disparo")
        self.assertTrue(res["realShotHit"], "Disparo real de laser deve ser detectado")
        self.assertFalse(res["dragDoubleHit"], "Arrasto de gatilho <120ms nao deve duplicar o disparo")

    def test_health_endpoint_o1_backlog_4096_and_dynamic_worker_scaling(self):
        """Valida /api/health em O(1), request_queue_size=4096, pre-warming, socket opts e 200+ Keep-Alive."""
        import socket
        from unittest import mock

        self.assertEqual(servidor_dev.ThreadedHTTPSServer.request_queue_size, 4096)
        self.assertGreaterEqual(servidor_dev.DEFAULT_MAX_WORKERS, 1024)

        # 1. Valida pre-warming imediato, escalonamento elástico e retração por idle_timeout no DynamicWorkerPool
        pool = servidor_dev.DynamicWorkerPool(min_workers=2, max_workers=16, idle_timeout=0.15)
        self.assertGreaterEqual(pool.stats()["current"], 2)
        self.assertEqual(pool.stats()["current"], 2)
        barrier = threading.Event()
        try:
            for _ in range(8):
                pool.submit(barrier.wait, 2.0)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and pool.stats()["active"] < 8:
                time.sleep(0.01)
            snap_busy = pool.stats()
            self.assertEqual(snap_busy["active"], 8)
            self.assertGreaterEqual(snap_busy["current"], 8)
            self.assertGreaterEqual(snap_busy["peak"], 8)
        finally:
            barrier.set()

        # Aguarda retração de workers ociosos acima de min_workers
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and pool.stats()["current"] > 2:
            time.sleep(0.02)
        self.assertEqual(pool.stats()["current"], 2)
        pool.shutdown(wait=True)

        # 2. Valida /api/health em O(1) e 200 conexões Keep-Alive HTTPS paralelas (acima de 128)
        cert_path, key_path = servidor_dev.gerar_certificado_autoassinado()
        ctx = servidor_dev.create_ssl_context(cert_path, key_path)
        client_ctx = ssl._create_unverified_context()
        prev_quiet = servidor_dev.DevRequestHandler.quiet_logs
        servidor_dev.DevRequestHandler.quiet_logs = True

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_db = Path(tmp_dir) / "health_sessions.db"
            httpd = servidor_dev.ThreadedHTTPSServer(
                ("127.0.0.1", 0),
                servidor_dev.DevRequestHandler,
                min_workers=16,
                max_workers=512,
                db_path=tmp_db,
            )
            self.assertGreaterEqual(httpd._executor.stats()["current"], 16)
            accepted_socket_opts: list[tuple[int, int]] = []
            orig_get_request = httpd.get_request

            def tracked_get_request():
                req_sock, client_addr = orig_get_request()
                accepted_socket_opts.append((
                    req_sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY),
                    req_sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE),
                ))
                return req_sock, client_addr

            httpd.get_request = tracked_get_request  # type: ignore[method-assign]
            port = httpd.server_address[1]
            httpd.wrap_ssl(ctx, do_handshake_on_connect=False)
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                conn = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=5)
                try:
                    with (
                        mock.patch.object(sqlite3, "connect", side_effect=AssertionError("SQLite chamado em /api/health")),
                        mock.patch.object(os, "stat", side_effect=AssertionError("os.stat chamado em /api/health")),
                    ):
                        conn.request("GET", "/api/health")
                        resp = conn.getresponse()
                        body = json.loads(resp.read().decode("utf-8"))
                        self.assertEqual(resp.status, 200)
                        self.assertIn("no-store", (resp.getheader("Cache-Control") or "").lower())
                        self.assertEqual(body["status"], "ok")
                        self.assertTrue(body["stateless"])
                        self.assertEqual(body["request_queue_size"], 4096)
                        self.assertIn("workers", body)
                        self.assertIn("queue", body)
                        self.assertIn("cache", body)
                        self.assertEqual(body["queue"]["capacity"], servidor_dev.SESSION_QUEUE_MAXSIZE)
                        self.assertTrue(accepted_socket_opts)
                        self.assertTrue(all(nodelay != 0 and keepalive != 0 for nodelay, keepalive in accepted_socket_opts))

                        conn.request("HEAD", "/api/health")
                        r_head = conn.getresponse()
                        self.assertEqual(len(r_head.read()), 0)
                        self.assertEqual(r_head.status, 200)
                finally:
                    conn.close()

                # Mantém 200 conexões Keep-Alive HTTPS simultâneas abertas sem starvation
                conns: list[http.client.HTTPSConnection] = []
                conns_lock = threading.Lock()
                try:
                    def open_and_probe(_idx: int) -> int:
                        c = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=5)
                        with conns_lock:
                            conns.append(c)
                        c.request("GET", "/api/health")
                        r = c.getresponse()
                        r.read()
                        return r.status

                    with ThreadPoolExecutor(max_workers=40) as open_pool:
                        statuses = list(open_pool.map(open_and_probe, range(200)))
                    for st in statuses:
                        self.assertEqual(st, 200)

                    self.assertGreaterEqual(httpd._executor.stats()["peak"], 129)
                finally:
                    for c in conns:
                        try:
                            c.close()
                        except Exception:
                            pass
            finally:
                servidor_dev.DevRequestHandler.quiet_logs = prev_quiet
                httpd.shutdown()
                httpd.server_close()

    def test_so_reuseport_multiprocess_cluster_and_stateless_api(self):
        """Valida múltiplos processos na mesma porta via SO_REUSEPORT com API estritamente stateless."""
        import multiprocessing
        import socket

        if not hasattr(socket, "SO_REUSEPORT"):
            self.skipTest("SO_REUSEPORT não disponível nesta plataforma")

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])

        cert_path, key_path = servidor_dev.gerar_certificado_autoassinado()
        client_ctx = ssl._create_unverified_context()
        mp_ctx = multiprocessing.get_context("spawn")

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_db = Path(tmp_dir) / "reuseport_sessions.db"
            procs = []
            for idx in range(2):
                p = mp_ctx.Process(
                    target=servidor_dev._worker_process_main,
                    args=(
                        "127.0.0.1",
                        port,
                        True,
                        str(cert_path),
                        str(key_path),
                        True,
                        True,
                        64,
                        str(tmp_db),
                        "none",
                        1,
                    ),
                    name=f"test-reuseport-{idx}",
                )
                p.start()
                procs.append(p)

            try:
                # Aguarda os processos worker subirem e responderem em /api/health
                deadline = time.monotonic() + 8.0
                ready = False
                while time.monotonic() < deadline:
                    try:
                        c = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=1.0)
                        c.request("GET", "/api/health")
                        r = c.getresponse()
                        data = json.loads(r.read().decode("utf-8"))
                        c.close()
                        if r.status == 200 and data.get("reuse_port") is True and data.get("stateless") is True:
                            ready = True
                            break
                    except Exception:
                        time.sleep(0.05)
                self.assertTrue(ready, "Cluster SO_REUSEPORT não respondeu a tempo")

                # Dispara requisições paralelas em novas conexões TCP para exercer distribuição pelo kernel
                def call_health_and_targets(idx: int) -> tuple[int, int, int]:
                    conn = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=5)
                    try:
                        conn.request("GET", "/api/health")
                        rh = conn.getresponse()
                        hdata = json.loads(rh.read().decode("utf-8"))
                        conn.request("GET", "/api/targets")
                        rt = conn.getresponse()
                        rt.read()
                        return rh.status, rt.status, int(hdata["pid"])
                    finally:
                        conn.close()

                with ThreadPoolExecutor(max_workers=16) as pool:
                    results = list(pool.map(call_health_and_targets, range(48)))

                self.assertTrue(all(st_h == 200 and st_t == 200 for st_h, st_t, _ in results))
                pids_seen = {pid for _, _, pid in results}
                self.assertGreaterEqual(len(pids_seen), 1)
                self.assertTrue(pids_seen.issubset({p.pid for p in procs}))
            finally:
                for p in procs:
                    if p.is_alive():
                        p.terminate()
                for p in procs:
                    p.join(timeout=5.0)

    def test_adaptive_keepalive_governance_pressure_and_headers(self):
        """Valida Keep-Alive: timeout=5, max=100, ratio 0.75, timeout adaptativo >50% e reciclagem limpa."""
        self.assertEqual(servidor_dev.KEEPALIVE_PRESSURE_RATIO, 0.75)
        self.assertEqual(servidor_dev.DEFAULT_MAX_WORKERS, 2048)
        self.assertGreaterEqual(servidor_dev.DEFAULT_CLUSTER_MAX_WORKERS, 1024)

        cert_path, key_path = servidor_dev.gerar_certificado_autoassinado()
        ctx = servidor_dev.create_ssl_context(cert_path, key_path)
        client_ctx = ssl._create_unverified_context()
        prev_quiet = servidor_dev.DevRequestHandler.quiet_logs
        servidor_dev.DevRequestHandler.quiet_logs = True

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_db = Path(tmp_dir) / "keepalive_gov.db"
            httpd = servidor_dev.ThreadedHTTPSServer(
                ("127.0.0.1", 0),
                servidor_dev.DevRequestHandler,
                min_workers=16,
                max_workers=16,
                db_path=tmp_db,
            )
            port = httpd.server_address[1]
            httpd.wrap_ssl(ctx, do_handshake_on_connect=False)
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()

            try:
                # 1. Sob baixa utilização, resposta persistente inclui Keep-Alive: timeout=5, max=100
                conn = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=5)
                try:
                    conn.request("GET", "/api/health")
                    r1 = conn.getresponse()
                    r1.read()
                    self.assertEqual(r1.status, 200)
                    self.assertEqual(r1.getheader("Keep-Alive"), "timeout=5, max=100")
                    self.assertNotEqual((r1.getheader("Connection") or "").lower(), "close")
                finally:
                    conn.close()

                # 2. Acima de 50% de utilização (9/16 workers ocupados), timeout adaptativo cai para reciclagem rápida
                release_9 = threading.Event()
                for _ in range(9):
                    httpd._executor.submit(release_9.wait, 5.0)
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline and httpd._executor.stats()["active"] < 9:
                    time.sleep(0.01)
                self.assertGreater(httpd._executor.stats()["active"], 8)

                timeouts_seen: list[float] = []
                orig_handle_one = servidor_dev.DevRequestHandler.handle_one_request

                def spy_handle_one(handler_self):
                    orig_handle_one(handler_self)
                    timeouts_seen.append(float(handler_self.request.gettimeout()))

                servidor_dev.DevRequestHandler.handle_one_request = spy_handle_one  # type: ignore[method-assign]
                try:
                    c_adapt = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=5)
                    try:
                        c_adapt.request("GET", "/api/health")
                        ra = c_adapt.getresponse()
                        ra.read()
                        self.assertEqual(ra.status, 200)
                        self.assertEqual(ra.getheader("Keep-Alive"), "timeout=5, max=100")
                        self.assertTrue(timeouts_seen)
                        self.assertEqual(timeouts_seen[-1], servidor_dev.KEEPALIVE_PRESSURE_TIMEOUT_SECONDS)
                    finally:
                        c_adapt.close()
                finally:
                    servidor_dev.DevRequestHandler.handle_one_request = orig_handle_one  # type: ignore[method-assign]
                    release_9.set()

                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline and httpd._executor.stats()["active"] > 0:
                    time.sleep(0.01)

                # 3. Em >= 75% de utilização (12/16 ocupados), responde com Connection: close para reciclagem limpa
                release_12 = threading.Event()
                for _ in range(12):
                    httpd._executor.submit(release_12.wait, 5.0)
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline and httpd._executor.stats()["active"] < 12:
                    time.sleep(0.01)
                self.assertGreaterEqual(httpd._executor.stats()["active"], 12)

                try:
                    c_press = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=5)
                    try:
                        c_press.request("GET", "/api/health")
                        rp = c_press.getresponse()
                        rp.read()
                        self.assertEqual(rp.status, 200)
                        self.assertEqual((rp.getheader("Connection") or "").lower(), "close")
                        self.assertIsNone(rp.getheader("Keep-Alive"))
                    finally:
                        c_press.close()
                finally:
                    release_12.set()
            finally:
                servidor_dev.DevRequestHandler.quiet_logs = prev_quiet
                httpd.shutdown()
                httpd.server_close()

    def test_health_micro_ttl_lockfree_invalidation_and_date_cache_1hz(self):
        """Valida micro-TTL 50ms lock-free em /api/health, invalidação no close() e cache 1 Hz de Date."""
        import email.utils
        from unittest import mock

        self.assertEqual(servidor_dev.HEALTH_CACHE_TTL_SECONDS, 0.05)

        cert_path, key_path = servidor_dev.gerar_certificado_autoassinado()
        ctx = servidor_dev.create_ssl_context(cert_path, key_path)
        client_ctx = ssl._create_unverified_context()
        prev_quiet = servidor_dev.DevRequestHandler.quiet_logs
        servidor_dev.DevRequestHandler.quiet_logs = True

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_db = Path(tmp_dir) / "health_cache.db"
            httpd = servidor_dev.ThreadedHTTPSServer(
                ("127.0.0.1", 0),
                servidor_dev.DevRequestHandler,
                min_workers=16,
                max_workers=64,
                db_path=tmp_db,
            )
            port = httpd.server_address[1]
            httpd.wrap_ssl(ctx, do_handshake_on_connect=False)
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()

            try:
                conn = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=5)
                try:
                    # 1. Primeira requisição aquece o snapshot de 50 ms
                    conn.request("GET", "/api/health")
                    r1 = conn.getresponse()
                    b1 = json.loads(r1.read().decode("utf-8"))
                    self.assertEqual(r1.status, 200)
                    self.assertEqual(b1["status"], "ok")
                    self.assertTrue(r1.getheader("Date"))

                    # 2. Dentro da janela de 50 ms, /api/health não adquire locks de pool/shards/cache nem chama json.dumps
                    with (
                        mock.patch.object(httpd._executor, "stats", side_effect=AssertionError("pool.stats chamado no hot-path")),
                        mock.patch.object(httpd.session_store, "queue_depth", side_effect=AssertionError("queue_depth chamado no hot-path")),
                        mock.patch.object(httpd.cache_backend, "stats", side_effect=AssertionError("cache.stats chamado no hot-path")),
                        mock.patch.object(servidor_dev.json, "dumps", side_effect=AssertionError("json.dumps chamado no hot-path")),
                    ):
                        for _ in range(5):
                            conn.request("GET", "/api/health")
                            rh = conn.getresponse()
                            payload = json.loads(rh.read().decode("utf-8"))
                            self.assertEqual(rh.status, 200)
                            self.assertEqual(payload["status"], "ok")
                            for key in ("status", "stateless", "workers", "queue", "cache"):
                                self.assertIn(key, payload)

                    # 3. Após expirar o micro-TTL (60 ms), o snapshot renova automaticamente
                    time.sleep(0.06)
                    conn.request("GET", "/api/health")
                    r2 = conn.getresponse()
                    b2 = json.loads(r2.read().decode("utf-8"))
                    self.assertEqual(r2.status, 200)
                    self.assertGreater(b2["uptime_s"], b1["uptime_s"])

                    # 4. Invalidação imediata quando SessionStore.close() é chamado (mesmo dentro dos 50 ms)
                    httpd.session_store.close()
                    st_closed, view_closed, _ = httpd.health_snapshot_bytes()
                    closed_payload = json.loads(bytes(view_closed).decode("utf-8"))
                    self.assertEqual(st_closed, 503)
                    self.assertEqual(closed_payload["status"], "unhealthy")
                    self.assertTrue(closed_payload["queue"]["closed"])

                    # 5. Cache 1 Hz de DevRequestHandler.date_time_string()
                    handler_stub = servidor_dev.DevRequestHandler.__new__(servidor_dev.DevRequestHandler)
                    servidor_dev._DATE_HEADER_CACHE = (-1, "")
                    with mock.patch.object(
                        email.utils,
                        "formatdate",
                        wraps=email.utils.formatdate,
                    ) as mock_formatdate, mock.patch.object(
                        servidor_dev.time,
                        "time",
                        return_value=1764000000.25,
                    ):
                        d1 = handler_stub.date_time_string()
                        d2 = handler_stub.date_time_string()
                        d3 = handler_stub.date_time_string()
                        self.assertEqual(d1, d2)
                        self.assertEqual(d2, d3)
                        self.assertEqual(mock_formatdate.call_count, 1)

                        # Timestamp explícito não usa o cache de 1 Hz
                        d_explicit = handler_stub.date_time_string(1700000000.0)
                        self.assertNotEqual(d_explicit, d1)
                        self.assertEqual(mock_formatdate.call_count, 2)
                finally:
                    conn.close()
            finally:
                servidor_dev.DevRequestHandler.quiet_logs = prev_quiet
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
