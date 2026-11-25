#!/usr/bin/env python3
"""
Teste Automatizado de Carga Escalatória e Endurance com Locust (100 -> 250 -> 500 -> 1000 Usuários)
- Inicia cluster HTTPS multi-processo SO_REUSEPORT (6 workers x 1024 threads + 4 Shards SQLite WAL + Cache Híbrido L1/L2)
- Executa o Locust em modo headless com rampa escalatória (100 -> 250 -> 500 -> 1000 usuários a 100 usuários/s)
- Valida tolerância estrita de zero erros (0.00%), throughput (RPS), latências (P50, P95, P99) e drenagem nos shards WAL
- Gera relatório JSON (test/output/locust_step_report.json), CSVs e gráfico comparativo (test/output/locust_escalabilidade_backend.png)
"""

import http.client
import json
import multiprocessing
import os
try:
    import resource
except ImportError:  # pragma: no cover
    resource = None  # type: ignore[assignment]
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-targets-virtu")

if resource is not None:
    try:
        _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        _target_nofile = min(65536, int(_hard)) if _hard > 0 else 65536
        if _target_nofile > _soft:
            resource.setrlimit(resource.RLIMIT_NOFILE, (_target_nofile, _hard))
    except (ValueError, OSError):
        pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = BASE_DIR / "edit" / "tools"
OUTPUT_DIR = BASE_DIR / "test" / "output"
sys.path.insert(0, str(TOOLS_DIR))

import servidor_dev  # noqa: E402

BASELINE_100_USERS_RPS = 959.8


def _gerar_grafico_escalabilidade(report: dict, output_png: Path) -> None:
    """Plota o gráfico comparativo de eficiência e escalabilidade do back-end por estágio e endpoint."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stages = report.get("stages", [])
    endpoints = report.get("endpoints", {})

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.8), dpi=120)
    fig.patch.set_facecolor("#161c24")

    for ax in (ax1, ax2):
        ax.set_facecolor("#212b36")
        ax.tick_params(colors="#e2e8f0")
        for spine in ax.spines.values():
            spine.set_color("#334155")

    # Painel 1: Rampa Escalatória 100 -> 250 -> 500 -> 1000 Usuários vs RPS e Latência
    if stages:
        users = [f"{s['users']} usr\n(Est. {s['stage']})" for s in stages]
        rps_vals = [s["stage_rps"] for s in stages]
        p50_vals = [s["p50_ms"] for s in stages]
        p95_vals = [s["p95_ms"] for s in stages]

        bars = ax1.bar(users, rps_vals, color="#4caf50", width=0.48, label="Throughput Cluster (req/s)")
        ax1.axhline(
            BASELINE_100_USERS_RPS,
            color="#94a3b8",
            linestyle=":",
            linewidth=1.4,
            label=f"Baseline Monoprocesso 100u ({BASELINE_100_USERS_RPS:.0f} req/s)",
        )
        ax1.set_title("Escalabilidade Cluster SO_REUSEPORT (100 → 1.000 Usuários)", color="#f8fafc", fontsize=11, fontweight="bold")
        ax1.set_ylabel("Throughput (Requisições / s)", color="#4caf50", fontsize=10)
        ax1.grid(axis="y", linestyle="--", alpha=0.25, color="#94a3b8")

        max_rps = max(rps_vals) if rps_vals else 1.0
        for bar, val in zip(bars, rps_vals):
            ax1.text(
                bar.get_x() + bar.get_width() / 2.0,
                bar.get_height() + max_rps * 0.02,
                f"{val:.0f} rps",
                ha="center",
                va="bottom",
                color="#f8fafc",
                fontsize=8.5,
                fontweight="bold",
            )

        ax1_lat = ax1.twinx()
        ax1_lat.plot(users, p50_vals, color="#38bdf8", marker="o", linewidth=2, label="P50 (ms)")
        ax1_lat.plot(users, p95_vals, color="#f59e0b", marker="s", linewidth=2, label="P95 (ms)")
        ax1_lat.set_ylabel("Latência Acumulada (ms)", color="#f59e0b", fontsize=10)
        ax1_lat.tick_params(colors="#e2e8f0")
        for spine in ax1_lat.spines.values():
            spine.set_color("#334155")

        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax1_lat.get_legend_handles_labels()
        ax1_lat.legend(
            lines1 + lines2,
            labels1 + labels2,
            loc="upper left",
            facecolor="#161c24",
            edgecolor="#334155",
            labelcolor="#f8fafc",
            fontsize=7.8,
        )

    # Painel 2: Latência P50 vs P95 por Endpoint do Back-end
    if endpoints:
        sorted_eps = sorted(endpoints.items(), key=lambda kv: kv[0])
        labels = [k.split(" ", 1)[1] if " " in k else k for k, _ in sorted_eps]
        p50_ep = [v["p50_ms"] for _, v in sorted_eps]
        p95_ep = [v["p95_ms"] for _, v in sorted_eps]

        y_pos = list(range(len(labels)))
        bar_h = 0.36
        ax2.barh([y - bar_h / 2 for y in y_pos], p50_ep, height=bar_h, color="#38bdf8", label="Mediana P50 (ms)")
        ax2.barh([y + bar_h / 2 for y in y_pos], p95_ep, height=bar_h, color="#f59e0b", label="Percentil P95 (ms)")
        ax2.set_yticks(y_pos)
        ax2.set_yticklabels(labels, fontsize=8.2, color="#e2e8f0")
        ax2.invert_yaxis()
        ax2.set_xlabel("Tempo de Resposta (ms)", color="#e2e8f0", fontsize=10)
        ax2.set_title("Eficiência por Rota (Ping, Cache L1/L2, ETag 304, Shards WAL)", color="#f8fafc", fontsize=11, fontweight="bold")
        ax2.grid(axis="x", linestyle="--", alpha=0.25, color="#94a3b8")
        ax2.legend(loc="lower right", facecolor="#161c24", edgecolor="#334155", labelcolor="#f8fafc", fontsize=8)

    overall = report.get("overall", {})
    fig.suptitle(
        f"Locust Load Test (100 → 250 → 500 → 1.000 Usuários @ 100/s) | "
        f"Total: {overall.get('total_requests', 0)} reqs | "
        f"Média: {overall.get('total_rps', 0):.1f} req/s | "
        f"P50: {overall.get('p50_ms', 0):.1f}ms | P95: {overall.get('p95_ms', 0):.1f}ms | "
        f"Erros: {overall.get('error_rate_pct', 0):.2f}%",
        color="#f8fafc",
        fontsize=11.0,
        fontweight="bold",
        y=0.98,
    )
    plt.tight_layout(rect=[0, 0, 1, 0.93])
    fig.savefig(output_png, facecolor=fig.get_facecolor())
    plt.close(fig)


class TestLocustEscalatoryBackend(unittest.TestCase):
    def test_locust_escalatory_step_load_100_to_1000_users(self) -> None:
        """Executa teste escalatório no Locust (100 -> 250 -> 500 -> 1000 usuários) com tolerância zero erros."""
        cert_path, key_path = servidor_dev.gerar_certificado_autoassinado()
        client_ctx = ssl._create_unverified_context()

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        report_json_path = OUTPUT_DIR / "locust_step_report.json"
        csv_prefix = OUTPUT_DIR / "locust_backend"
        chart_png_path = OUTPUT_DIR / "locust_escalabilidade_backend.png"

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])

        num_procs = 6 if hasattr(socket, "SO_REUSEPORT") else 1
        workers_per_proc = (
            servidor_dev.DEFAULT_CLUSTER_MAX_WORKERS
            if num_procs > 1
            else servidor_dev.DEFAULT_MAX_WORKERS
        )
        num_shards = 4
        mp_ctx = multiprocessing.get_context("spawn")

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_db = Path(tmp_dir) / "sessions_locust.db"
            procs: list[multiprocessing.Process] = []
            for idx in range(num_procs):
                proc = mp_ctx.Process(
                    target=servidor_dev._worker_process_main,
                    args=(
                        "127.0.0.1",
                        port,
                        True,
                        str(cert_path),
                        str(key_path),
                        True,
                        True,
                        workers_per_proc,
                        str(tmp_db),
                        "none",
                        num_shards,
                    ),
                    name=f"locust-backend-{idx}",
                )
                proc.start()
                procs.append(proc)

            try:
                deadline = time.monotonic() + 10.0
                healthy = False
                while time.monotonic() < deadline:
                    try:
                        conn = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=1.0)
                        conn.request("GET", "/api/health")
                        r = conn.getresponse()
                        r.read()
                        conn.close()
                        if r.status == 200:
                            healthy = True
                            break
                    except Exception:
                        time.sleep(0.05)
                self.assertTrue(healthy, "Cluster HTTPS não ficou saudável a tempo")

                locust_bin = BASE_DIR / ".venv" / "bin" / "locust"
                locustfile = BASE_DIR / "test" / "locustfile.py"

                env = os.environ.copy()
                env["LOCUST_STAGE_USERS"] = "100,250,500,1000"
                env["LOCUST_SPAWN_RATE"] = "100"
                env["LOCUST_STAGE_DURATION"] = "6.0"
                env["LOCUST_WAIT_MIN"] = "0.15"
                env["LOCUST_WAIT_MAX"] = "0.35"
                env["LOCUST_HTTP_CONCURRENCY"] = "2"
                env["LOCUST_POOL_CONCURRENCY"] = "64"
                env["LOCUST_STEP_REPORT_PATH"] = str(report_json_path)
                env["NO_PROXY"] = "127.0.0.1,localhost"
                env["no_proxy"] = "127.0.0.1,localhost"

                cmd = [
                    str(locust_bin),
                    "-f", str(locustfile),
                    "--headless",
                    "--processes", "4",
                    "--host", f"https://127.0.0.1:{port}",
                    "--csv", str(csv_prefix),
                    "--only-summary",
                ]
                proc_locust = subprocess.run(
                    cmd,
                    cwd=str(BASE_DIR),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                self.assertEqual(
                    proc_locust.returncode,
                    0,
                    f"Locust falhou (code={proc_locust.returncode}):\nSTDOUT:\n{proc_locust.stdout}\nSTDERR:\n{proc_locust.stderr}",
                )
            finally:
                for p in procs:
                    if p.is_alive():
                        p.terminate()
                for p in procs:
                    p.join(timeout=8.0)

            self.assertTrue(report_json_path.exists(), "Relatório JSON do Locust não foi gerado")
            report = json.loads(report_json_path.read_text(encoding="utf-8"))

            stages = report["stages"]
            overall = report["overall"]
            endpoints = report["endpoints"]

            _gerar_grafico_escalabilidade(report, chart_png_path)
            self.assertTrue(chart_png_path.exists(), "Gráfico comparativo de escalabilidade não foi gerado")

            print("\n" + "=" * 82)
            print("RELATÓRIO DE TESTE ESCALATÓRIO LOCUST — CLUSTER HTTPS TARGETS_VIRTU (1.000 USUÁRIOS)")
            print("=" * 82)
            for st in stages:
                print(
                    f"  • Estágio {st['stage']} ({st['users']:>4} usuários): "
                    f"{st['stage_requests']:>6} reqs | {st['stage_rps']:>7.1f} req/s | "
                    f"P50={st['p50_ms']:>5.1f}ms | P95={st['p95_ms']:>5.1f}ms | "
                    f"Erros={st['stage_failures']}"
                )
            print("-" * 82)
            print(
                f"  • TOTAL ACUMULADO: {overall['total_requests']} reqs | "
                f"Throughput Médio={overall['total_rps']:.1f} req/s | "
                f"Avg={overall['avg_ms']:.2f}ms | P50={overall['p50_ms']:.1f}ms | "
                f"P95={overall['p95_ms']:.1f}ms | P99={overall['p99_ms']:.1f}ms | "
                f"Falhas={overall['total_failures']} ({overall['error_rate_pct']:.2f}%)"
            )
            print("=" * 82)

            # Validações estritas de SLA e Tolerância Zero Erros
            self.assertEqual([s["users"] for s in stages], [100, 250, 500, 1000])
            self.assertEqual(overall["total_failures"], 0, f"Houve falhas sob carga: {report}")
            self.assertEqual(overall["error_rate_pct"], 0.0)
            self.assertGreaterEqual(overall["total_requests"], 5000)
            self.assertGreaterEqual(overall["total_rps"], 1000.0)
            self.assertLessEqual(overall["p50_ms"], 35.0, f"Latência P50 acima do SLA: {overall['p50_ms']}ms")
            self.assertLess(overall["p95_ms"], 1000.0, f"Latência P95 acima do SLA: {overall['p95_ms']}ms")
            self.assertLessEqual(stages[0]["p50_ms"], 10.0, f"P50 Estágio 1 acima do SLA: {stages[0]['p50_ms']}ms")
            self.assertLessEqual(stages[1]["p50_ms"], 15.0, f"P50 Estágio 2 acima do SLA: {stages[1]['p50_ms']}ms")

            # Valida inclusão dos endpoints de ping (/ e /api/health)
            self.assertIn("GET 0_Ping_GET /api/health [O(1)]", endpoints)
            self.assertIn("GET 0_Ping_GET / [200/304]", endpoints)
            self.assertEqual(endpoints["GET 0_Ping_GET /api/health [O(1)]"]["failures"], 0)
            self.assertEqual(endpoints["GET 0_Ping_GET / [200/304]"]["failures"], 0)

            # Verifica persistência real nos shards SQLite WAL de todas as sessões enviadas
            post_ep = endpoints.get("POST 5_POST /api/sessions [202 Async WAL]", {})
            expected_posts = post_ep.get("requests", 0)
            self.assertGreater(expected_posts, 100, "Poucas sessões de treino enviadas durante o teste")

            persisted_count = 0
            for shard_idx in range(num_shards):
                shard_path = tmp_db.parent / f"{tmp_db.stem}_shard_{shard_idx}.db"
                if shard_path.exists():
                    conn = sqlite3.connect(str(shard_path))
                    try:
                        persisted_count += conn.execute("SELECT COUNT(*) FROM sessions;").fetchone()[0]
                    finally:
                        conn.close()

            expected_min = min(expected_posts, servidor_dev.MAX_STORED_SESSIONS)
            self.assertGreaterEqual(
                persisted_count,
                expected_min,
                f"Drenagem SQLite WAL sharded incompleta: esperado >= {expected_min}, gravado {persisted_count}",
            )

    def test_endurance_1h_shape_configuration(self) -> None:
        """Valida perfil padrão Endurance 1h (3600s) com rampa 100->250->500->1000 a 100 usuários/s."""
        clean_env = os.environ.copy()
        for key in (
            "LOCUST_STAGE_DURATION",
            "LOCUST_TOTAL_DURATION",
            "LOCUST_STAGE_USERS",
            "LOCUST_SPAWN_RATE",
        ):
            clean_env.pop(key, None)

        code = (
            "import json, sys; sys.path.insert(0, 'test'); "
            "from locust import FastHttpUser; "
            "import locustfile as lf; "
            "shape = lf.EscalatoryLoadShape(); "
            "points = {}; "
            "[points.__setitem__(str(t), shape.tick()) for t in (0, 15, 30, 45, 60, 1800, 3599, 3600) "
            "if not setattr(shape, 'get_run_time', (lambda sec: (lambda: float(sec)))(t))]; "
            "print(json.dumps({"
            "'is_fast': issubclass(lf.TargetsVirtuUser, FastHttpUser), "
            "'stage_s': lf._STAGE_DURATION_S, "
            "'total_s': lf._TOTAL_DURATION_S, "
            "'spawn_rate': lf._SPAWN_RATE, "
            "'points': points}))"
        )
        raw = subprocess.check_output(
            [str(BASE_DIR / ".venv" / "bin" / "python3"), "-c", code],
            cwd=str(BASE_DIR),
            env=clean_env,
            text=True,
        ).strip()
        data = json.loads(raw)
        self.assertTrue(data["is_fast"], "TargetsVirtuUser deve herdar de FastHttpUser")
        self.assertEqual(data["stage_s"], 15.0)
        self.assertEqual(data["total_s"], 3600.0)
        self.assertEqual(data["spawn_rate"], 100.0)
        self.assertEqual(data["points"]["0"], [100, 100.0])
        self.assertEqual(data["points"]["15"], [250, 100.0])
        self.assertEqual(data["points"]["30"], [500, 100.0])
        self.assertEqual(data["points"]["45"], [1000, 100.0])
        self.assertEqual(data["points"]["60"], [1000, 100.0])
        self.assertEqual(data["points"]["1800"], [1000, 100.0])
        self.assertEqual(data["points"]["3599"], [1000, 100.0])
        self.assertIsNone(data["points"]["3600"])


if __name__ == "__main__":
    unittest.main()
