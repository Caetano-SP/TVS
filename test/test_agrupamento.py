#!/usr/bin/env python3
"""
Teste Automatizado de Métricas de Agrupamento Balístico e Precisão
Valida as métricas matemáticas especificadas em Regras/especifica_o_de_requisitos_e_depend_ncias.md:
- Distância Euclidiana ao Centro
- Extreme Spread (ES) via scipy.spatial.distance
- Mean Radius (MR)
- Ponto Médio de Impacto (MPI)
- Geração da elipse de dispersão e salvamento em test/output/
"""

import unittest
from pathlib import Path
import numpy as np
import scipy.spatial.distance as dist
from scipy.stats import chi2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "test" / "output"

def calcular_metricas_balisticas(impactos, centro_alvo=(300, 400)):
    """
    Calcula as métricas de precisão de uma série de disparos.
    impactos: array-like de shape (N, 2) com coordenadas (X, Y)
    centro_alvo: tupla (X0, Y0)
    """
    pts = np.asarray(impactos, dtype=np.float64)
    n = len(pts)
    if n == 0:
        return {}
    
    # 1. Ponto Médio de Impacto (MPI / Baricentro)
    mpi = np.mean(pts, axis=0)
    
    # 2. Distância Euclidiana de cada tiro ao centro do alvo
    c0 = np.asarray(centro_alvo, dtype=np.float64)
    dist_ao_centro = np.linalg.norm(pts - c0, axis=1)
    
    # 3. Extreme Spread (ES): Maior distância entre quaisquer dois tiros da série
    if n >= 2:
        matriz_dist = dist.pdist(pts, metric="euclidean")
        extreme_spread = float(np.max(matriz_dist))
    else:
        extreme_spread = 0.0
        
    # 4. Mean Radius (MR): Distância média dos tiros em relação ao baricentro (MPI)
    dist_ao_mpi = np.linalg.norm(pts - mpi, axis=1)
    mean_radius = float(np.mean(dist_ao_mpi))
    
    return {
        "n": n,
        "mpi": mpi,
        "extreme_spread": extreme_spread,
        "mean_radius": mean_radius,
        "dist_ao_centro_media": float(np.mean(dist_ao_centro)),
        "dist_ao_centro_min": float(np.min(dist_ao_centro)),
        "dist_ao_centro_max": float(np.max(dist_ao_centro))
    }

class TestAgrupamentoBalistico(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def test_calculo_metricas_sinteticas(self):
        """Valida precisão matemática das métricas com coordenadas conhecidas."""
        # 4 pontos formando um quadrado de 100x100 pixels centrado em (300, 400)
        pontos = np.array([
            [250, 350],
            [350, 350],
            [350, 450],
            [250, 450]
        ], dtype=np.float64)

        metricas = calcular_metricas_balisticas(pontos, centro_alvo=(300, 400))

        # MPI deve ser exatamente (300, 400)
        np.testing.assert_allclose(metricas["mpi"], [300.0, 400.0], atol=1e-5)

        # Extreme Spread deve ser a diagonal do quadrado: sqrt(100^2 + 100^2) = 141.421356
        esperado_es = np.sqrt(100**2 + 100**2)
        self.assertAlmostEqual(metricas["extreme_spread"], esperado_es, places=4)

        # Mean Radius deve ser a distância do centro a qualquer vértice: sqrt(50^2 + 50^2) = 70.710678
        esperado_mr = np.sqrt(50**2 + 50**2)
        self.assertAlmostEqual(metricas["mean_radius"], esperado_mr, places=4)

    def test_serie_tiros_e_plotagem_elipse(self):
        """Gera uma série simulada de 10 disparos, valida métricas scipy e gera gráfico da elipse."""
        np.random.seed(42)
        centroide_esperado = np.array([295.0, 405.0])
        # Dispersão normal com desvio padrão de 18 pixels em X e 25 pixels em Y
        tiros = np.random.normal(loc=centroide_esperado, scale=[18.0, 25.0], size=(12, 2))

        metricas = calcular_metricas_balisticas(tiros, centro_alvo=(300, 400))
        self.assertEqual(metricas["n"], 12)
        self.assertGreater(metricas["extreme_spread"], 0)
        self.assertGreater(metricas["mean_radius"], 0)

        # Plotagem gráfica com matplotlib e scipy
        fig, ax = plt.subplots(figsize=(6, 8), dpi=120)
        ax.set_facecolor("#111111")
        fig.patch.set_facecolor("#181818")

        # Desenhar anéis concêntricos do alvo de teste
        centro_x, centro_y = 300, 400
        for r, score in [(240, "5"), (180, "7"), (120, "9"), (60, "10"), (25, "X")]:
            circ = plt.Circle((centro_x, centro_y), r, color="#333333", fill=False, linewidth=1, linestyle="--")
            ax.add_patch(circ)
            ax.text(centro_x, centro_y - r + 8, score, color="#666666", fontsize=8, ha="center")

        # Plotar furos numerados
        for i, (tx, ty) in enumerate(tiros, start=1):
            ax.scatter(tx, ty, s=120, color="#d32f2f", edgecolors="#ffffff", linewidths=1.5, zorder=5)
            ax.text(tx + 4, ty + 4, str(i), color="#ffffff", fontsize=9, fontweight="bold", zorder=6)

        # Plotar MPI (Baricentro)
        mpi = metricas["mpi"]
        ax.scatter(mpi[0], mpi[1], s=160, color="#00e676", marker="+", linewidths=2.5, zorder=7, label="MPI")

        # Calcular elipse de dispersão (intervalo de confiança 95% via chi2)
        cov = np.cov(tiros, rowvar=False)
        vals, vecs = np.linalg.eigh(cov)
        ordem = vals.argsort()[::-1]
        vals = vals[ordem]
        vecs = vecs[:, ordem]
        angulo = np.degrees(np.arctan2(*vecs[:, 0][::-1]))
        
        # Fator de escala para 95% de confiança (2 graus de liberdade)
        escala_95 = np.sqrt(chi2.ppf(0.95, df=2))
        largura, altura = 2 * escala_95 * np.sqrt(np.maximum(vals, 1e-6))

        elipse = Ellipse(xy=mpi, width=largura, height=altura, angle=angulo,
                         edgecolor="#00e676", facecolor="#00e676", alpha=0.15,
                         linewidth=1.5, linestyle=":", label="Elipse 95%")
        ax.add_patch(elipse)

        ax.set_xlim(0, 600)
        ax.set_ylim(800, 0) # Invertido para padrão de coordenadas de tela/câmera
        ax.set_title(f"Série Dry-Fire: ES={metricas['extreme_spread']:.1f}px | MR={metricas['mean_radius']:.1f}px", color="#ffffff", pad=12)
        ax.legend(loc="upper right", facecolor="#222222", edgecolor="#444444", labelcolor="#ffffff")
        ax.grid(True, color="#222222", linestyle=":")

        grafico_path = OUTPUT_DIR / "agrupamento_serie.png"
        fig.savefig(str(grafico_path), bbox_inches="tight")
        plt.close(fig)

        self.assertTrue(grafico_path.exists(), "Falha ao salvar o gráfico de agrupamento.")
        print(f"\n[SUCESSO] Gráfico de métricas balísticas salvo em: {grafico_path}")
        print(f"[INFO] Disparos: {metricas['n']} | ES: {metricas['extreme_spread']:.2f}px | MR: {metricas['mean_radius']:.2f}px | MPI: ({mpi[0]:.1f}, {mpi[1]:.1f})")

if __name__ == "__main__":
    unittest.main()

