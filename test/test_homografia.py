#!/usr/bin/env python3
"""
Teste de Detecção ArUco e Retificação de Perspectiva (Homografia 2D)
Utiliza as imagens de alvos oficiais do Mantis localizadas em lib/
"""

import sys
import unittest
from pathlib import Path
import numpy as np
import cv2

# Caminhos base
BASE_DIR = Path(__file__).resolve().parent.parent
LIB_MANTIS = BASE_DIR / "lib" / "Mantis-Target-Analysis-main"
IMAGES_DIR = LIB_MANTIS / "images"
OUTPUT_DIR = BASE_DIR / "test" / "output"

TARGET_W, TARGET_H = 600, 800

class TestHomografiaMantis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        # Configurar dicionário ArUco padrão DICT_4X4_50
        cls.aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        cls.aruco_params = cv2.aruco.DetectorParameters()
        cls.detector = cv2.aruco.ArucoDetector(cls.aruco_dict, cls.aruco_params)

        # Destino canônico (retificado) 600x800
        cls.dst_pts = np.array([
            [0, 0],
            [TARGET_W - 1, 0],
            [TARGET_W - 1, TARGET_H - 1],
            [0, TARGET_H - 1]
        ], dtype=np.float32)

    def test_deteccao_marcadores_alvo_ipsc(self):
        """Testa se os marcadores do alvo IPSC são detectados com sucesso."""
        ipsc_img_path = IMAGES_DIR / "Mantis_LaserAcademy_Targets_2_2026-06-10-07.png"
        self.assertTrue(ipsc_img_path.exists(), f"Imagem não encontrada: {ipsc_img_path}")

        img = cv2.imread(str(ipsc_img_path))
        self.assertIsNotNone(img, "Falha ao carregar a imagem do alvo.")

        corners, ids, _ = self.detector.detectMarkers(img)
        self.assertIsNotNone(ids, "Nenhum marcador ArUco detectado no alvo IPSC.")
        self.assertGreaterEqual(len(ids), 4, f"Esperado pelo menos 4 marcadores, detectados: {len(ids)}")

        ids_flat = ids.flatten().tolist()
        print(f"\n[INFO] Alvo IPSC: Marcadores detectados: {ids_flat}")

        # Identificar os 4 cantos pelos centros dos marcadores
        # Ordenar os 4 marcadores mais externos: top-left, top-right, bottom-right, bottom-left
        marker_centers = []
        for i, c in enumerate(corners):
            pts = c[0]
            cx = float(np.mean(pts[:, 0]))
            cy = float(np.mean(pts[:, 1]))
            marker_centers.append((ids_flat[i], cx, cy, pts))

        # Classificar por coordenadas geométricas
        # Top-left (menor x+y), Bottom-right (maior x+y)
        # Top-right (maior x - y), Bottom-left (menor x - y)
        marker_centers_sorted_by_y = sorted(marker_centers, key=lambda m: m[2])
        top_two = sorted(marker_centers_sorted_by_y[:2], key=lambda m: m[1]) # TL, TR
        bottom_two = sorted(marker_centers_sorted_by_y[-2:], key=lambda m: m[1]) # BL, BR

        tl = top_two[0]
        tr = top_two[1]
        br = bottom_two[1]
        bl = bottom_two[0]

        print(f"[INFO] Cantos identificados:")
        print(f"  Top-Left     (ID {tl[0]}): centro ({tl[1]:.1f}, {tl[2]:.1f})")
        print(f"  Top-Right    (ID {tr[0]}): centro ({tr[1]:.1f}, {tr[2]:.1f})")
        print(f"  Bottom-Right (ID {br[0]}): centro ({br[1]:.1f}, {br[2]:.1f})")
        print(f"  Bottom-Left  (ID {bl[0]}): centro ({bl[1]:.1f}, {bl[2]:.1f})")

        src_pts = np.array([
            [tl[1], tl[2]],
            [tr[1], tr[2]],
            [br[1], br[2]],
            [bl[1], bl[2]]
        ], dtype=np.float32)

        # Calcular matriz de Homografia (Perspectiva 2D)
        matrix = cv2.getPerspectiveTransform(src_pts, self.dst_pts)
        warped = cv2.warpPerspective(img, matrix, (TARGET_W, TARGET_H))

        self.assertEqual(warped.shape, (TARGET_H, TARGET_W, 3), "Dimensões da imagem retificada incorretas.")

        output_path = OUTPUT_DIR / "ipsc_retificado_600x800.png"
        cv2.imwrite(str(output_path), warped)
        print(f"[SUCESSO] Imagem do alvo retificado salva em: {output_path}")

    def test_deteccao_marcadores_alvo_controle(self):
        """Testa o alvo de controle Mantis 01."""
        ctrl_img_path = IMAGES_DIR / "Mantis_LaserAcademy_Targets_2_2026-06-10-01.png"
        img = cv2.imread(str(ctrl_img_path))
        self.assertIsNotNone(img, "Falha ao carregar a imagem de controle.")

        corners, ids, _ = self.detector.detectMarkers(img)
        self.assertIsNotNone(ids, "Nenhum marcador detectado no alvo de controle.")
        print(f"\n[INFO] Alvo Controle: Marcadores detectados: {ids.flatten().tolist()}")

if __name__ == "__main__":
    unittest.main()

