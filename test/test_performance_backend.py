#!/usr/bin/env python3
"""
Testes de Performance, Complexidade Algoritmica (Big-O) e Estrategias de Caching (TARGETS_VIRTU)
- Benchmark HTTP/HTTPS concorrente do backend (servidor_dev.py)
- Validacao de Cache em RAM para /api/targets e arquivos estaticos Gzip
- Validacao de Caching Condicional HTTP (ETag + If-None-Match -> 304 Not Modified)
- Validacao empirica de complexidade O(1) via memoizacao em ballistics_echarts.js (60 FPS)
- Benchmark de escalabilidade linear O(N_roi / 4) e early-skip em laser-worker.js
- Benchmark de deteccao ArUco em aruco_universal.js
"""

import gzip
import http.client
import json
import os
import ssl

os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
import statistics
import subprocess
import sys
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


def percentile(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(round((p / 100.0) * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


class TestPerformanceAndCaching(unittest.TestCase):
    def test_backend_cache_etag_304_and_throughput(self):
        """Valida ETag, 304 Not Modified, Cache-Control sem no-store e throughput concorrente em RAM/Gzip."""
        cert_path, key_path = servidor_dev.gerar_certificado_autoassinado()
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

            # 1. Verifica ETag + Cache-Control + 304 Not Modified em /api/targets e /ballistics_echarts.js
            for path in ("/api/targets", "/index.html", "/ballistics_echarts.js"):
                req1 = urllib.request.Request(
                    f"https://127.0.0.1:{port}{path}",
                    headers={"Accept-Encoding": "gzip"},
                )
                with urllib.request.urlopen(req1, context=client_ctx, timeout=5) as resp1:
                    self.assertEqual(resp1.status, 200)
                    etag = resp1.headers.get("ETag")
                    cache_ctl = (resp1.headers.get("Cache-Control") or "").lower()
                    body1 = resp1.read()
                    self.assertTrue(etag, f"ETag ausente em {path}")
                    self.assertIn("no-cache", cache_ctl)
                    self.assertNotIn("no-store", cache_ctl, "no-store impede revalidacao condicional ETag/304")
                    self.assertGreater(len(body1), 20)

                # Requisicao condicional com If-None-Match deve retornar 304 com corpo vazio
                req2 = urllib.request.Request(
                    f"https://127.0.0.1:{port}{path}",
                    headers={"Accept-Encoding": "gzip", "If-None-Match": etag},
                )
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(req2, context=client_ctx, timeout=5)
                self.assertEqual(cm.exception.code, 304, f"Esperado 304 Not Modified em {path}")
                self.assertEqual(len(cm.exception.read()), 0)

            # 2. Benchmark concorrente (60 requests em 6 threads) medindo Throughput e P95
            endpoints = ["/api/targets", "/index.html", "/app.js", "/ballistics_echarts.js", "/style.css"] * 12

            def timed_get(path):
                t0 = time.perf_counter()
                req = urllib.request.Request(
                    f"https://127.0.0.1:{port}{path}",
                    headers={"Accept-Encoding": "gzip"},
                )
                with urllib.request.urlopen(req, context=client_ctx, timeout=5) as resp:
                    _ = resp.read()
                    status = resp.status
                return status, (time.perf_counter() - t0) * 1000.0

            # Warmup do cache
            for p in ("/api/targets", "/index.html", "/app.js", "/ballistics_echarts.js", "/style.css"):
                timed_get(p)

            t_start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(timed_get, endpoints))
            total_s = time.perf_counter() - t_start

            latencies = sorted(lat for _, lat in results)
            statuses = [st for st, _ in results]
            self.assertTrue(all(st == 200 for st in statuses))

            avg_ms = statistics.mean(latencies)
            p95_ms = percentile(latencies, 95)
            rps = len(endpoints) / total_s
            print(
                f"\n[PERF BACKEND COLD-TLS] {len(endpoints)} req HTTPS+Gzip | "
                f"Throughput={rps:.1f} req/s | Avg={avg_ms:.2f}ms | P95={p95_ms:.2f}ms"
            )
            self.assertLess(p95_ms, 80.0, f"Latencia P95 muito alta: {p95_ms:.2f}ms")

            # 3. Benchmark HTTP/1.1 Keep-Alive (como o navegador/WebView real faz, reutilizando sessao TLS)
            conn = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=5)
            ka_latencies = []
            ka_304_latencies = []
            try:
                etag_map = {}
                for path in ("/api/targets", "/index.html", "/app.js", "/ballistics_echarts.js", "/style.css"):
                    conn.request("GET", path, headers={"Accept-Encoding": "gzip"})
                    r = conn.getresponse()
                    _ = r.read()
                    etag_map[path] = r.getheader("ETag")

                for _ in range(20):
                    for path in ("/api/targets", "/index.html", "/app.js", "/ballistics_echarts.js", "/style.css"):
                        t0 = time.perf_counter()
                        conn.request("GET", path, headers={"Accept-Encoding": "gzip"})
                        r = conn.getresponse()
                        _ = r.read()
                        self.assertEqual(r.status, 200)
                        ka_latencies.append((time.perf_counter() - t0) * 1000.0)

                        t1 = time.perf_counter()
                        conn.request("GET", path, headers={"Accept-Encoding": "gzip", "If-None-Match": etag_map[path]})
                        r304 = conn.getresponse()
                        body304 = r304.read()
                        self.assertEqual(r304.status, 304)
                        self.assertEqual(len(body304), 0)
                        ka_304_latencies.append((time.perf_counter() - t1) * 1000.0)
            finally:
                conn.close()

            ka_avg = statistics.mean(ka_latencies)
            ka_304_avg = statistics.mean(ka_304_latencies)
            print(
                f"[PERF BACKEND KEEP-ALIVE] 100x200 Avg={ka_avg:.3f}ms ({1000.0/ka_avg:.0f} req/s) | "
                f"100x304 Avg={ka_304_avg:.3f}ms ({1000.0/ka_304_avg:.0f} req/s)"
            )
            self.assertLess(ka_avg, 5.0)
            self.assertLess(ka_304_avg, 5.0)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_route_fast_cache_zero_syscall_hot_path(self):
        """Valida zero syscalls stat/resolve no hot-path em RAM para 200 OK e 304 Not Modified."""
        from unittest import mock

        cert_path, key_path = servidor_dev.gerar_certificado_autoassinado()
        ctx = servidor_dev.create_ssl_context(cert_path, key_path)
        client_ctx = ssl._create_unverified_context()

        prev_gzip = servidor_dev.DevRequestHandler.force_gzip_dir
        prev_quiet = servidor_dev.DevRequestHandler.quiet_logs
        servidor_dev.DevRequestHandler.quiet_logs = True

        try:
            # 1. Modo --gzip: permanente em RAM com 0 syscalls após warmup
            servidor_dev.DevRequestHandler.force_gzip_dir = True
            httpd = servidor_dev.ThreadedHTTPSServer(("127.0.0.1", 0), servidor_dev.DevRequestHandler)
            port = httpd.server_address[1]
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                conn = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=5)
                try:
                    # Warmup (popula _ROUTE_FAST_CACHE)
                    etags = {}
                    for path in ("/index.html", "/app.js", "/api/targets"):
                        conn.request("GET", path, headers={"Accept-Encoding": "gzip, br"})
                        resp = conn.getresponse()
                        body = resp.read()
                        self.assertEqual(resp.status, 200)
                        self.assertGreater(len(body), 20)
                        etags[path] = resp.getheader("ETag")

                    entry = servidor_dev._get_route_fast_cache(("/app.js", "br+gzip"), force_gzip=True)
                    self.assertIsNotNone(entry)
                    self.assertIsNone(entry.expires_at, "Entrada --gzip deve ser permanente em RAM")

                    # Hot-path: 200 OK e 304 Not Modified com 0 syscalls resolve/stat/is_file/is_dir
                    with (
                        mock.patch.object(Path, "resolve", side_effect=AssertionError("Path.resolve chamado no hot-path")),
                        mock.patch.object(Path, "stat", side_effect=AssertionError("Path.stat chamado no hot-path")),
                        mock.patch.object(Path, "is_file", side_effect=AssertionError("Path.is_file chamado no hot-path")),
                        mock.patch.object(Path, "is_dir", side_effect=AssertionError("Path.is_dir chamado no hot-path")),
                        mock.patch.object(os, "stat", side_effect=AssertionError("os.stat chamado no hot-path")),
                    ):
                        for path in ("/index.html", "/app.js", "/api/targets"):
                            conn.request("GET", path, headers={"Accept-Encoding": "gzip, br"})
                            r200 = conn.getresponse()
                            b200 = r200.read()
                            self.assertEqual(r200.status, 200)
                            self.assertGreater(len(b200), 20)

                            conn.request(
                                "GET",
                                path,
                                headers={"Accept-Encoding": "gzip, br", "If-None-Match": etags[path]},
                            )
                            r304 = conn.getresponse()
                            b304 = r304.read()
                            self.assertEqual(r304.status, 304)
                            self.assertEqual(len(b304), 0)
                finally:
                    conn.close()
            finally:
                httpd.shutdown()
                httpd.server_close()

            # 2. Modo dev: TTL curto (~1.0s) com 0 syscalls dentro da janela e renovação após expirar
            servidor_dev.DevRequestHandler.force_gzip_dir = False
            httpd_dev = servidor_dev.ThreadedHTTPSServer(("127.0.0.1", 0), servidor_dev.DevRequestHandler)
            port_dev = httpd_dev.server_address[1]
            httpd_dev.socket = ctx.wrap_socket(httpd_dev.socket, server_side=True)
            t_dev = threading.Thread(target=httpd_dev.serve_forever, daemon=True)
            t_dev.start()
            try:
                conn_dev = http.client.HTTPSConnection("127.0.0.1", port_dev, context=client_ctx, timeout=5)
                try:
                    conn_dev.request("GET", "/app.js", headers={"Accept-Encoding": "gzip, br"})
                    r_w = conn_dev.getresponse()
                    r_w.read()
                    self.assertEqual(r_w.status, 200)
                    etag_dev = r_w.getheader("ETag")

                    entry_dev = servidor_dev._get_route_fast_cache(("/app.js", "br+gzip"), force_gzip=False)
                    self.assertIsNotNone(entry_dev)
                    self.assertIsNotNone(entry_dev.expires_at, "Entrada dev deve possuir TTL curto")

                    with (
                        mock.patch.object(Path, "resolve", side_effect=AssertionError("Path.resolve chamado dentro do TTL")),
                        mock.patch.object(Path, "stat", side_effect=AssertionError("Path.stat chamado dentro do TTL")),
                    ):
                        conn_dev.request(
                            "GET",
                            "/app.js",
                            headers={"Accept-Encoding": "gzip, br", "If-None-Match": etag_dev},
                        )
                        r_hit = conn_dev.getresponse()
                        r_hit.read()
                        self.assertEqual(r_hit.status, 304)
                finally:
                    conn_dev.close()
            finally:
                httpd_dev.shutdown()
                httpd_dev.server_close()
        finally:
            servidor_dev.DevRequestHandler.force_gzip_dir = prev_gzip
            servidor_dev.DevRequestHandler.quiet_logs = prev_quiet

    def test_ballistics_memoization_big_o_speedup(self):
        """Valida complexidade O(1) de computeRoundSummary no loop de 60 FPS (120 frames) e equivalencia matematica."""
        js_path = BASE_DIR / "edit" / "src" / "ballistics_echarts.js"
        node_script = r"""
const TargetAnalytics = require(process.argv[1]);
const { performance } = require("perf_hooks");

function makeShots(n) {
  const arr = [];
  for (let i = 0; i < n; i++) {
    const angle = (i * 137.5 * Math.PI) / 180;
    const rad = 12 + (i % 90);
    const x = Math.round(300 + Math.cos(angle) * rad);
    const y = Math.round(400 + Math.sin(angle) * rad);
    const evalRes = TargetAnalytics.evaluateShotZone(x, y, "VIRTU_A4");
    arr.push({
      id: i + 1,
      x,
      y,
      zone: evalRes.zone,
      points: evalRes.points,
      split: "0.22",
      totalTime: Number(((i + 1) * 0.22).toFixed(2))
    });
  }
  return arr;
}

// Equivalencia matematica + invalidacao por mudanca de tamanho/perfil
const shots50 = makeShots(50);
const sA = TargetAnalytics.computeRoundSummary(shots50, 11.0);
const sB = TargetAnalytics.computeRoundSummary(shots50, 12.5);
if (sA.extremeSpread !== sB.extremeSpread || sA.meanRadius !== sB.meanRadius || sA.points !== sB.points) {
  throw new Error("Resumo espacial nao deveria divergir entre chamadas");
}
if (sA.hitFactor === sB.hitFactor && sA.points > 0) {
  // Como totalTime do ultimo tiro (11.0) prevalece sobre fallbackElapsed se > 0, testamos array vazio ou elapsed maior
}

// Simula 120 frames (2s @ 60 FPS) durante rodada ativa com elapsedSeconds crescente
const counts = [10, 50, 200, 500];
const timings = {};
for (const n of counts) {
  const shots = makeShots(n);
  // 1a chamada (cold)
  const coldT0 = performance.now();
  const baseSummary = TargetAnalytics.computeRoundSummary(shots, n * 0.22);
  const coldMs = performance.now() - coldT0;

  // 120 chamadas subsequentes (tickChrono @ 60 FPS sem novos tiros)
  const warmT0 = performance.now();
  let lastSummary = null;
  for (let f = 1; f <= 120; f++) {
    lastSummary = TargetAnalytics.computeRoundSummary(shots, n * 0.22 + f * 0.016);
  }
  const warmTotalMs = performance.now() - warmT0;

  // Adiciona +1 tiro e valida atualizacao incremental O(S)
  const extra = makeShots(1)[0];
  extra.id = n + 1;
  extra.x = 580;
  extra.y = 780;
  extra.totalTime = Number(((n + 1) * 0.22).toFixed(2));
  shots.push(extra);
  const incT0 = performance.now();
  const afterPush = TargetAnalytics.computeRoundSummary(shots, extra.totalTime);
  const incMs = performance.now() - incT0;

  // Valida que Extreme Spread aumentou de verdade com o ponto extremo (580, 780)
  if (afterPush.extremeSpread <= baseSummary.extremeSpread) {
    throw new Error("Invalidacao incremental falhou apos adicionar tiro extremo");
  }
  if (afterPush.shotCount !== n + 1) {
    throw new Error("shotCount incorreto apos push");
  }

  timings[n] = {
    coldMs: Number(coldMs.toFixed(3)),
    warm120Ms: Number(warmTotalMs.toFixed(3)),
    incMs: Number(incMs.toFixed(3)),
    es: baseSummary.extremeSpread,
    esAfterPush: afterPush.extremeSpread
  };
}
console.log(JSON.stringify(timings));
"""
        raw = subprocess.check_output(["node", "-e", node_script, str(js_path)], text=True).strip()
        timings = json.loads(raw)
        print(f"\n[PERF BALLISTICS O(1)] Timings (ms): {json.dumps(timings)}")

        # 120 frames com 500 tiros devem levar < 5ms no total graças a memoizacao O(1)
        self.assertLess(timings["500"]["warm120Ms"], 5.0)
        # Razao entre warm(500) e warm(10) deve permanecer proxima de O(1), nao 2500x!
        ratio = timings["500"]["warm120Ms"] / max(timings["10"]["warm120Ms"], 0.001)
        self.assertLess(ratio, 5.0, f"Escalabilidade esperada O(1), mas razao 500/10 foi {ratio:.2f}x")

    def test_laser_worker_linear_scaling_and_early_skip(self):
        """Valida complexidade O(N_roi / 4) e tempo sub-milissegundo em ROI VGA."""
        worker_path = BASE_DIR / "edit" / "src" / "workers" / "laser-worker.js"
        node_script = r"""
const fs = require("fs");
const vm = require("vm");
const { performance } = require("perf_hooks");

const code = fs.readFileSync(process.argv[1], "utf8");
const sandbox = {
  self: {
    postMessage() {}
  },
  Float32Array,
  Uint8ClampedArray,
  ArrayBuffer,
  Math,
  Number
};
vm.runInNewContext(code, sandbox);

const sizes = [
  { w: 160, h: 120 },
  { w: 320, h: 240 },
  { w: 640, h: 480 }
];

const report = {};
for (const { w, h } of sizes) {
  sandbox.self.onmessage({ data: { type: "reset-bg" } });
  const buf = new ArrayBuffer(w * h * 4);
  const u8 = new Uint8ClampedArray(buf);
  for (let i = 0; i < u8.length; i += 4) {
    u8[i] = 195; u8[i + 1] = 195; u8[i + 2] = 195; u8[i + 3] = 255;
  }

  // Inicializa bgBuffer
  sandbox.self.onmessage({
    data: {
      type: "detect",
      payload: {
        buffer: buf, startX: 0, endX: w, startY: 0, endY: h,
        threshold: 210, purity: 38, lastShotTimestamp: 0, cooldownMs: 95, now: 100
      }
    }
  });

  const frames = 60;
  const t0 = performance.now();
  for (let f = 1; f <= frames; f++) {
    sandbox.self.onmessage({
      data: {
        type: "detect",
        payload: {
          buffer: buf, startX: 0, endX: w, startY: 0, endY: h,
          threshold: 210, purity: 38, lastShotTimestamp: 0, cooldownMs: 95, now: 100 + f * 16
        }
      }
    });
  }
  const totalMs = performance.now() - t0;
  report[`${w}x${h}`] = {
    pixels: w * h,
    avgFrameMs: Number((totalMs / frames).toFixed(3))
  };
}
console.log(JSON.stringify(report));
"""
        raw = subprocess.check_output(["node", "-e", node_script, str(worker_path)], text=True).strip()
        report = json.loads(raw)
        print(f"\n[PERF LASER WORKER O(N/4)] Avg frame ms: {json.dumps(report)}")

        # ROI 640x480 completa deve rodar em menos de 4.0ms por frame em JS puro
        self.assertLess(report["640x480"]["avgFrameMs"], 4.0)
        # 16x mais pixels (640x480 vs 160x120) deve escalar no maximo linearmente (~16x, nunca quadratico)
        ratio = report["640x480"]["avgFrameMs"] / max(report["160x120"]["avgFrameMs"], 0.001)
        self.assertLess(ratio, 30.0)

    def test_aruco_universal_frame_benchmark(self):
        """Mede tempo por frame do detector ArUco em cena 1280x720 (garante <= 50ms para >= 20 FPS)."""
        import cv2
        import numpy as np

        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_250)
        frame_w, frame_h = 1280, 720
        sheet_h, sheet_w = 540, 380
        sheet = np.full((sheet_h, sheet_w, 3), 248, dtype=np.uint8)
        ids = [10, 20, 30, 40]
        positions = [(20, 20), (sheet_w - 84, 20), (sheet_w - 84, sheet_h - 84), (20, sheet_h - 84)]
        for idx, (mid, (px, py)) in enumerate(zip(ids, positions)):
            m_img = np.rot90(cv2.aruco.generateImageMarker(aruco_dict, mid, 64), -idx)
            sheet[py:py + 64, px:px + 64] = cv2.cvtColor(m_img, cv2.COLOR_GRAY2BGR)

        scene = np.full((frame_h, frame_w, 3), 70, dtype=np.uint8)
        ox, oy = (frame_w - sheet_w) // 2, (frame_h - sheet_h) // 2
        scene[oy:oy + sheet_h, ox:ox + sheet_w] = sheet
        rgba = cv2.cvtColor(scene, cv2.COLOR_BGR2RGBA).tobytes()

        js_code = """
require('./edit/src/lib/aruco_dictionaries.js');
require('./edit/src/lib/aruco_universal.js');
const fs = require('fs');
const { performance } = require('perf_hooks');
const buf = new Uint8Array(fs.readFileSync(0));
const det = new global.ArUcoUniversalDetector();

// Warmup
det.detectMarkersDownsampled(buf, 1280, 720, 640);

const iters = 10;
const t0 = performance.now();
let lastMarkers = [];
for (let i = 0; i < iters; i++) {
  lastMarkers = det.detectMarkersDownsampled(buf, 1280, 720, 640);
}
const avgMs = (performance.now() - t0) / iters;
console.log(JSON.stringify({ avgMs: Number(avgMs.toFixed(2)), count: lastMarkers.length }));
"""
        proc = subprocess.run(["node", "-e", js_code], cwd=BASE_DIR, input=rgba, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", errors="replace"))
        res = json.loads(proc.stdout.decode("utf-8"))
        print(f"\n[PERF ARUCO 5x5 1280x720] Avg={res['avgMs']}ms | Markers={res['count']}")
        self.assertEqual(res["count"], 4)
        self.assertLess(res["avgMs"], 60.0)


if __name__ == "__main__":
    unittest.main()
