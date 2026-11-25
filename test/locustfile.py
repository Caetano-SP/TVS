#!/usr/bin/env python3
"""
Locustfile para Teste Escalatório e Endurance 1h (1.000 Usuários) do Back-end (TARGETS_VIRTU)
- Simula o ciclo real de atiradores concorrentes (Ping / e /api/health, Bootstrap Brotli/Gzip,
  Revalidação ETag/304, Catálogo /api/targets em RAM, Imagens Imutáveis, Persistência Assíncrona POST/GET /api/sessions).
- Implementa LoadTestShape escalatório em 4 estágios (100 -> 250 -> 500 -> 1000 usuários simultâneos,
  spawn rate padrão 100 usuários/s e endurance de 3600s).
- Coleta telemetria por estágio (Throughput RPS, P50, P95, P99, Taxa de Erro) e exporta JSON em test/output/.
"""

import json
import os
import random
try:
    import resource
except ImportError:  # pragma: no cover
    resource = None  # type: ignore[assignment]
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from geventhttpclient.client import HTTPClientPool
from locust import FastHttpUser, LoadTestShape, between, events, task
from locust.contrib.fasthttp import insecure_ssl_context_factory
from locust.runners import WorkerRunner

if resource is not None:
    try:
        _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        _target_nofile = min(65536, int(_hard)) if _hard > 0 else 65536
        if _target_nofile > _soft:
            resource.setrlimit(resource.RLIMIT_NOFILE, (_target_nofile, _hard))
    except (ValueError, OSError):
        pass

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "test" / "output"

STATIC_BOOTSTRAP_PATHS = (
    "/",
    "/index.html",
    "/style.css",
    "/app.js",
    "/ballistics_echarts.js",
    "/workers/aruco-worker.js",
    "/workers/laser-worker.js",
    "/lib/aruco_dictionaries.js",
    "/lib/aruco_universal.js",
)

REVALIDATE_PATHS = (
    "/index.html",
    "/style.css",
    "/app.js",
    "/ballistics_echarts.js",
)

TARGET_PROFILES = ("VIRTU_A4", "IPSC", "IDPA", "B27")

_WAIT_MIN = float(os.environ.get("LOCUST_WAIT_MIN", "0.05"))
_WAIT_MAX = float(os.environ.get("LOCUST_WAIT_MAX", "0.15"))
_STAGE_USERS_RAW = os.environ.get("LOCUST_STAGE_USERS", "100,250,500,1000")
_STAGE_USERS = [int(x.strip()) for x in _STAGE_USERS_RAW.split(",") if x.strip()]
_SPAWN_RATE = float(os.environ.get("LOCUST_SPAWN_RATE", "100"))
_HTTP_CONCURRENCY = int(os.environ.get("LOCUST_HTTP_CONCURRENCY", "4"))
_POOL_CONCURRENCY = int(os.environ.get("LOCUST_POOL_CONCURRENCY", "64"))
_RAW_STAGE_DURATION = os.environ.get("LOCUST_STAGE_DURATION")
_STAGE_DURATION_S = float(_RAW_STAGE_DURATION) if _RAW_STAGE_DURATION is not None else 15.0
_RAW_TOTAL_DURATION = os.environ.get("LOCUST_TOTAL_DURATION")
if _RAW_TOTAL_DURATION is not None:
    _TOTAL_DURATION_S = float(_RAW_TOTAL_DURATION)
elif _RAW_STAGE_DURATION is not None:
    _TOTAL_DURATION_S = max(1.0, len(_STAGE_USERS) * _STAGE_DURATION_S)
else:
    _TOTAL_DURATION_S = 3600.0

_STAGE_METRICS: list[dict[str, Any]] = []
_LAST_RECORDED_STAGE_IDX = -1
_PREV_TOTAL_REQS = 0
_PREV_TOTAL_FAILS = 0
_PREV_STAGE_TS = 0.0


def _capture_stage_snapshot(env: Any, stage_idx: int, target_users: int) -> None:
    """Registra métricas incrementais e acumuladas ao final de cada degrau de carga."""
    global _PREV_TOTAL_REQS, _PREV_TOTAL_FAILS, _PREV_STAGE_TS
    if env is None or env.stats is None:
        return

    now = time.monotonic()
    total = env.stats.total
    cur_reqs = total.num_requests
    cur_fails = total.num_failures
    dt = max(0.001, now - _PREV_STAGE_TS) if _PREV_STAGE_TS > 0 else _STAGE_DURATION_S

    delta_reqs = max(0, cur_reqs - _PREV_TOTAL_REQS)
    delta_fails = max(0, cur_fails - _PREV_TOTAL_FAILS)
    stage_rps = delta_reqs / dt

    _PREV_TOTAL_REQS = cur_reqs
    _PREV_TOTAL_FAILS = cur_fails
    _PREV_STAGE_TS = now

    snapshot = {
        "stage": stage_idx + 1,
        "users": target_users,
        "stage_requests": delta_reqs,
        "stage_failures": delta_fails,
        "stage_rps": round(stage_rps, 2),
        "cumulative_requests": cur_reqs,
        "cumulative_failures": cur_fails,
        "cumulative_rps": round(total.total_rps, 2),
        "avg_ms": round(total.avg_response_time, 2),
        "p50_ms": round(total.get_response_time_percentile(0.50) or 0.0, 2),
        "p95_ms": round(total.get_response_time_percentile(0.95) or 0.0, 2),
        "p99_ms": round(total.get_response_time_percentile(0.99) or 0.0, 2),
        "error_rate_pct": round((delta_fails / delta_reqs * 100.0) if delta_reqs > 0 else 0.0, 4),
    }
    _STAGE_METRICS.append(snapshot)


@events.test_start.add_listener
def _on_test_start(environment: Any, **_kwargs: Any) -> None:
    global _PREV_TOTAL_REQS, _PREV_TOTAL_FAILS, _PREV_STAGE_TS, _LAST_RECORDED_STAGE_IDX
    _STAGE_METRICS.clear()
    _PREV_TOTAL_REQS = 0
    _PREV_TOTAL_FAILS = 0
    _PREV_STAGE_TS = time.monotonic()
    _LAST_RECORDED_STAGE_IDX = -1


@events.quitting.add_listener
def _on_quitting(environment: Any, **_kwargs: Any) -> None:
    try:
        TargetsVirtuUser.client_pool.close()
    except Exception:
        pass
    if isinstance(getattr(environment, "runner", None), WorkerRunner):
        return
    global _LAST_RECORDED_STAGE_IDX
    if _LAST_RECORDED_STAGE_IDX < len(_STAGE_USERS) - 1 and _STAGE_USERS:
        last_idx = len(_STAGE_USERS) - 1
        _capture_stage_snapshot(environment, last_idx, _STAGE_USERS[last_idx])
        _LAST_RECORDED_STAGE_IDX = last_idx

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = Path(
        os.environ.get("LOCUST_STEP_REPORT_PATH", str(OUTPUT_DIR / "locust_step_report.json"))
    )
    total = environment.stats.total
    endpoints_summary = {}
    for (name, method), entry in environment.stats.entries.items():
        if entry.num_requests > 0:
            endpoints_summary[f"{method} {name}"] = {
                "requests": entry.num_requests,
                "failures": entry.num_failures,
                "rps": round(entry.total_rps, 2),
                "avg_ms": round(entry.avg_response_time, 2),
                "p50_ms": round(entry.get_response_time_percentile(0.50) or 0.0, 2),
                "p95_ms": round(entry.get_response_time_percentile(0.95) or 0.0, 2),
                "p99_ms": round(entry.get_response_time_percentile(0.99) or 0.0, 2),
            }

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stage_duration_s": _STAGE_DURATION_S,
        "total_duration_s": _TOTAL_DURATION_S,
        "stages": _STAGE_METRICS,
        "overall": {
            "total_requests": total.num_requests,
            "total_failures": total.num_failures,
            "total_rps": round(total.total_rps, 2),
            "avg_ms": round(total.avg_response_time, 2),
            "p50_ms": round(total.get_response_time_percentile(0.50) or 0.0, 2),
            "p95_ms": round(total.get_response_time_percentile(0.95) or 0.0, 2),
            "p99_ms": round(total.get_response_time_percentile(0.99) or 0.0, 2),
            "error_rate_pct": round(
                (total.num_failures / total.num_requests * 100.0) if total.num_requests > 0 else 0.0,
                4,
            ),
        },
        "endpoints": endpoints_summary,
    }
    report_path.write_bytes(json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"))


class TargetsVirtuUser(FastHttpUser):
    """Simula um dispositivo (Smartphone/Desktop) conectado ao backend TARGETS_VIRTU via geventhttpclient."""

    wait_time = between(_WAIT_MIN, _WAIT_MAX)
    insecure = True
    concurrency = _HTTP_CONCURRENCY
    connection_timeout = 15.0
    network_timeout = 20.0
    default_headers = {"Connection": "keep-alive"}
    client_pool = HTTPClientPool(
        concurrency=_POOL_CONCURRENCY,
        insecure=True,
        ssl_context_factory=insecure_ssl_context_factory,
        connection_timeout=15.0,
        network_timeout=20.0,
    )

    def on_start(self) -> None:
        self.etags: dict[str, str] = {}
        self.target_urls: list[str] = []
        self._bootstrap_client()

    def _bootstrap_client(self) -> None:
        headers = {"Accept-Encoding": "gzip, br"}
        with self.client.get(
            "/api/health",
            name="0_Ping_GET /api/health [O(1)]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Bootstrap /api/health falhou com status {resp.status_code}")

        for path in STATIC_BOOTSTRAP_PATHS:
            with self.client.get(
                path,
                headers=headers,
                name="1_Bootstrap_Static [200 br/gz]",
                catch_response=True,
            ) as resp:
                if resp.status_code == 200:
                    etag = resp.headers.get("ETag")
                    if etag:
                        self.etags[path] = etag
                    resp.success()
                else:
                    resp.failure(f"Bootstrap {path} falhou com status {resp.status_code}")

        with self.client.get(
            "/api/targets",
            headers=headers,
            name="2_GET /api/targets [200 RAM]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                etag = resp.headers.get("ETag")
                if etag:
                    self.etags["/api/targets"] = etag
                try:
                    items = resp.json()
                    if isinstance(items, list):
                        self.target_urls = [item["url"] for item in items if "url" in item]
                except Exception:
                    pass
                resp.success()
            else:
                resp.failure(f"GET /api/targets falhou com status {resp.status_code}")

    @task(3)
    def ping_root_and_health(self) -> None:
        """Executa sondas de ping em /api/health (O(1)) e / (ETag/304)."""
        with self.client.get(
            "/api/health",
            name="0_Ping_GET /api/health [O(1)]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if data.get("status") in ("ok", "degraded") and data.get("stateless") is True:
                        resp.success()
                    else:
                        resp.failure(f"/api/health inválido: {data}")
                except Exception as exc:
                    resp.failure(f"/api/health JSON inválido: {exc}")
            else:
                resp.failure(f"/api/health retornou {resp.status_code}")

        root_etag = self.etags.get("/")
        headers = {"Accept-Encoding": "gzip, br"}
        if root_etag:
            headers["If-None-Match"] = root_etag
        with self.client.get(
            "/",
            headers=headers,
            name="0_Ping_GET / [200/304]",
            catch_response=True,
        ) as resp:
            if root_etag and resp.status_code == 304:
                resp.success()
            elif resp.status_code == 200:
                new_etag = resp.headers.get("ETag")
                if new_etag:
                    self.etags["/"] = new_etag
                resp.success()
            else:
                resp.failure(f"GET / retornou {resp.status_code}")

    @task(5)
    def revalidate_static_assets_304(self) -> None:
        """Valida revalidação condicional HTTP (ETag -> 304 Not Modified em O(1))."""
        path = random.choice(REVALIDATE_PATHS)
        etag = self.etags.get(path)
        headers = {"Accept-Encoding": "gzip, br"}
        if etag:
            headers["If-None-Match"] = etag

        with self.client.get(
            path,
            headers=headers,
            name="3_Revalidate_Static [304 ETag]",
            catch_response=True,
        ) as resp:
            if etag and resp.status_code == 304:
                resp.success()
            elif resp.status_code == 200:
                new_etag = resp.headers.get("ETag")
                if new_etag:
                    self.etags[path] = new_etag
                resp.success()
            else:
                resp.failure(f"Revalidação de {path} retornou {resp.status_code}")

    @task(3)
    def poll_api_targets(self) -> None:
        """Alterna entre consulta direta e condicional de /api/targets em RAM."""
        etag = self.etags.get("/api/targets")
        use_conditional = bool(etag) and random.random() < 0.7
        headers = {"Accept-Encoding": "gzip, br"}
        if use_conditional and etag:
            headers["If-None-Match"] = etag
            req_name = "2_GET /api/targets [304 ETag]"
        else:
            req_name = "2_GET /api/targets [200 RAM]"

        with self.client.get(
            "/api/targets",
            headers=headers,
            name=req_name,
            catch_response=True,
        ) as resp:
            if use_conditional and resp.status_code == 304:
                resp.success()
            elif resp.status_code == 200:
                new_etag = resp.headers.get("ETag")
                if new_etag:
                    self.etags["/api/targets"] = new_etag
                resp.success()
            else:
                resp.failure(f"/api/targets retornou {resp.status_code}")

    @task(2)
    def fetch_immutable_target_image(self) -> None:
        """Carrega imagem oficial de alvo servida com Cache-Control: immutable e cache LRU RAM."""
        if not self.target_urls:
            return
        img_url = random.choice(self.target_urls[:5])
        with self.client.get(
            img_url,
            name="4_GET /mantis_images/* [Immutable]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                cc = (resp.headers.get("Cache-Control") or "").lower()
                if "immutable" in cc:
                    resp.success()
                else:
                    resp.failure(f"Cache-Control sem immutable: {cc}")
            else:
                resp.failure(f"Imagem {img_url} retornou {resp.status_code}")

    @task(2)
    def post_training_session(self) -> None:
        """Envia telemetria de uma série de disparos para persistência assíncrona em SQLite WAL."""
        shot_count = random.randint(5, 12)
        shots = [
            {
                "id": idx + 1,
                "x": random.randint(180, 420),
                "y": random.randint(220, 560),
                "split": f"{random.uniform(0.16, 0.35):.2f}",
            }
            for idx in range(shot_count)
        ]
        payload = {
            "session_id": f"locust-{uuid.uuid4().hex[:12]}",
            "target": random.choice(TARGET_PROFILES),
            "shot_count": shot_count,
            "shots": shots,
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        with self.client.post(
            "/api/sessions",
            data=body,
            headers={"Content-Type": "application/json"},
            name="5_POST /api/sessions [202 Async WAL]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 202:
                resp.success()
            else:
                resp.failure(f"POST /api/sessions retornou {resp.status_code}")

    @task(2)
    def get_recent_sessions(self) -> None:
        """Consulta as últimas sessões persistidas no SQLite WAL / cache de leitura."""
        with self.client.get(
            "/api/sessions?limit=20",
            name="6_GET /api/sessions?limit=20 [WAL Read]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"GET /api/sessions retornou {resp.status_code}")


class EscalatoryLoadShape(LoadTestShape):
    """
    Curva de carga escalatória por degraus + Endurance 1h (3600s):
    - Padrão (Endurance): rampa 100 -> 250 -> 500 -> 1000 usuários a 100 usuários/s
      (15s por degrau inicial) e sustentação de 1000 usuários até 3600s.
    - CI / Teste rápido: controlado por LOCUST_STAGE_DURATION e LOCUST_TOTAL_DURATION.
    """

    def tick(self) -> Optional[tuple[int, float]]:
        global _LAST_RECORDED_STAGE_IDX
        run_time = self.get_run_time()
        if run_time >= _TOTAL_DURATION_S or not _STAGE_USERS:
            return None

        raw_stage_idx = int(run_time // _STAGE_DURATION_S)
        stage_idx = min(raw_stage_idx, len(_STAGE_USERS) - 1)

        if stage_idx > _LAST_RECORDED_STAGE_IDX + 1 and (_LAST_RECORDED_STAGE_IDX + 1) < len(_STAGE_USERS):
            prev_idx = _LAST_RECORDED_STAGE_IDX + 1
            env = getattr(self, "runner", None)
            env_obj = getattr(env, "environment", None) if env is not None else None
            _capture_stage_snapshot(env_obj, prev_idx, _STAGE_USERS[prev_idx])
            _LAST_RECORDED_STAGE_IDX = prev_idx

        users = _STAGE_USERS[stage_idx]
        return users, float(_SPAWN_RATE)
