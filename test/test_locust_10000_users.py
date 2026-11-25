#!/usr/bin/env python3
"""
Teste de Estresse e Saturação Empírica no Locust (1.000 -> 2.500 -> 5.000 -> 10.000 Usuários)
- Inicia o cluster HTTPS multi-processo SO_REUSEPORT (6 workers x 1024 threads + 4 Shards SQLite WAL) SEM MODIFICAÇÃO no servidor.
- Executa Locust em modo headless com rampa acelerada (1000 -> 2500 -> 5000 -> 10000 usuários a 250 usuários/s).
- Coleta telemetria por estágio, diagnostica saturação, tempo de resposta e gera relatórios em test/output/locust_10000_*.
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

BASELINE_1000_USERS_RPS = 1330.9


def _gerar_grafico_estresse_10000(report: dict, output_png: Path) -> None:
    """Gera gráfico de estresse com a curva de 1.000 a 10.000 usuários simultâneos."""
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

    # Painel 1: Rampa de Estresse 1.000 -> 2.500 -> 5.000 -> 10.000 Usuários
    if stages:
        users = [f"{s['users']} usr\n(Est. {s['stage']})" for s in stages]
        rps_vals = [s["stage_rps"] for s in stages]
        p50_vals = [s["p50_ms"] for s in stages]
        p95_vals = [s["p95_ms"] for s in stages]

        bars = ax1.bar(users, rps_vals, color="#3b82f6", width=0.48, label="Throughput Cluster (req/s)")
        ax1.axhline(
            BASELINE_1000_USERS_RPS,
            color="#94a3b8",
            linestyle=":",
            linewidth=1.4,
            label=f"Baseline 1.000u ({BASELINE_1000_USERS_RPS:.0f} req/s)",
        )
        ax1.set_title("Estresse Extremo Cluster SO_REUSEPORT (1.000 → 10.000 Usuários)", color="#f8fafc", fontsize=11, fontweight="bold")
        ax1.set_ylabel("Throughput (Requisições / s)", color="#38bdf8", fontsize=10)
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
        ax1_lat.plot(users, p50_vals, color="#34d399", marker="o", linewidth=2, label="P50 (ms)")
        ax1_lat.plot(users, p95_vals, color="#f43f5e", marker="s", linewidth=2, label="P95 (ms)")
        ax1_lat.set_ylabel("Latência Acumulada (ms)", color="#f43f5e", fontsize=10)
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

    # Painel 2: Latência P50 vs P95 por Rota
    if endpoints:
        sorted_eps = sorted(endpoints.items(), key=lambda kv: kv[0])
        labels = [k.split(" ", 1)[1] if " " in k else k for k, _ in sorted_eps]
        p50_ep = [v["p50_ms"] for _, v in sorted_eps]
        p95_ep = [v["p95_ms"] for _, v in sorted_eps]

        y_pos = list(range(len(labels)))
        bar_h = 0.36
        ax2.barh([y - bar_h / 2 for y in y_pos], p50_ep, height=bar_h, color="#34d399", label="Mediana P50 (ms)")
        ax2.barh([y + bar_h / 2 for y in y_pos], p95_ep, height=bar_h, color="#f43f5e", label="Percentil P95 (ms)")
        ax2.set_yticks(y_pos)
        ax2.set_yticklabels(labels, fontsize=8.2, color="#e2e8f0")
        ax2.invert_yaxis()
        ax2.set_xlabel("Tempo de Resposta (ms)", color="#e2e8f0", fontsize=10)
        ax2.set_title("Latência Sob 10.000 Usuários por Rota", color="#f8fafc", fontsize=11, fontweight="bold")
        ax2.grid(axis="x", linestyle="--", alpha=0.25, color="#94a3b8")
        ax2.legend(loc="lower right", facecolor="#161c24", edgecolor="#334155", labelcolor="#f8fafc", fontsize=8)

    overall = report.get("overall", {})
    fig.suptitle(
        f"Teste de Estresse (1.000 → 2.500 → 5.000 → 10.000 Usuários @ 250/s) | "
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


class TestLocust10000UsersStress(unittest.TestCase):
    def test_locust_stress_1000_to_10000_users(self) -> None:
        """Executa teste de estresse de 1.000 a 10.000 usuários e audita comportamento sem modificação."""
        cert_path, key_path = servidor_dev.gerar_certificado_autoassinado()
        client_ctx = ssl._create_unverified_context()

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        report_json_path = OUTPUT_DIR / "locust_10000_step_report.json"
        csv_prefix = OUTPUT_DIR / "locust_10000_backend"
        chart_png_path = OUTPUT_DIR / "locust_10000_escalabilidade.png"

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])

        num_procs = 6 if hasattr(socket, "SO_REUSEPORT") else 1
        workers_per_proc = servidor_dev.DEFAULT_CLUSTER_MAX_WORKERS
        num_shards = 4
        mp_ctx = multiprocessing.get_context("spawn")

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_db = Path(tmp_dir) / "sessions_10000u.db"
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
                    name=f"locust-10k-backend-{idx}",
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
                env["LOCUST_STAGE_USERS"] = "1000,2500,5000,10000"
                env["LOCUST_SPAWN_RATE"] = "250"
                env["LOCUST_STAGE_DURATION"] = "8.0"
                env["LOCUST_WAIT_MIN"] = "0.15"
                env["LOCUST_WAIT_MAX"] = "0.35"
                env["LOCUST_HTTP_CONCURRENCY"] = "2"
                env["LOCUST_POOL_CONCURRENCY"] = "128"
                env["LOCUST_STEP_REPORT_PATH"] = str(report_json_path)
                env["NO_PROXY"] = "127.0.0.1,localhost"
                env["no_proxy"] = "127.0.0.1,localhost"

                cmd = [
                    str(locust_bin),
                    "-f", str(locustfile),
                    "--headless",
                    "--processes", "6",
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
                    timeout=180,
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

            self.assertTrue(report_json_path.exists(), "Relatório JSON 10k do Locust não foi gerado")
            report = json.loads(report_json_path.read_text(encoding="utf-8"))

            stages = report["stages"]
            overall = report["overall"]
            endpoints = report["endpoints"]

            _gerar_grafico_estresse_10000(report, chart_png_path)
            self.assertTrue(chart_png_path.exists(), "Gráfico comparativo de estresse não foi gerado")

            print("\n" + "=" * 86)
            print("RELATÓRIO DE ESTRESSE LOCUST (10.000 USUÁRIOS) — ZERO MODIFICAÇÃO NO BACK-END")
            print("=" * 86)
            for st in stages:
                print(
                    f"  • Estágio {st['stage']} ({st['users']:>5} usuários): "
                    f"{st['stage_requests']:>7} reqs | {st['stage_rps']:>8.1f} req/s | "
                    f"P50={st['p50_ms']:>5.1f}ms | P95={st['p95_ms']:>6.1f}ms | "
                    f"Erros={st['stage_failures']}"
                )
            print("-" * 86)
            print(
                f"  • TOTAL ACUMULADO: {overall['total_requests']} reqs | "
                f"Throughput Médio={overall['total_rps']:.1f} req/s | "
                f"Avg={overall['avg_ms']:.2f}ms | P50={overall['p50_ms']:.1f}ms | "
                f"P95={overall['p95_ms']:.1f}ms | P99={overall['p99_ms']:.1f}ms | "
                f"Falhas={overall['total_failures']} ({overall['error_rate_pct']:.2f}%)"
            )
            print("=" * 86)

            # Validações estruturais do teste de estresse
            self.assertEqual([s["users"] for s in stages], [1000, 2500, 5000, 10000])
            self.assertGreaterEqual(overall["total_requests"], 10000)

            # Audita contagem nos shards WAL
            persisted_count = 0
            for shard_idx in range(num_shards):
                shard_path = tmp_db.parent / f"{tmp_db.stem}_shard_{shard_idx}.db"
                if shard_path.exists():
                    conn = sqlite3.connect(str(shard_path))
                    try:
                        persisted_count += conn.execute("SELECT COUNT(*) FROM sessions;").fetchone()[0]
                    finally:
                        conn.close()

            post_ep = endpoints.get("POST 5_POST /api/sessions [202 Async WAL]", {})
            expected_posts = post_ep.get("requests", 0)
            expected_min = min(expected_posts, servidor_dev.MAX_STORED_SESSIONS)
            self.assertGreaterEqual(
                persisted_count,
                expected_min,
                f"Drenagem WAL incompleta em 10k: esperado >= {expected_min}, gravado {persisted_count}",
            )


if __name__ == "__main__":
    unittest.main()
