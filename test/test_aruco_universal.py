#!/usr/bin/env python3
"""
Teste Automatizado de Paridade ArUco (cv2.aruco vs ArUcoUniversalDetector JS).
Valida dicionarios 4x4, 5x5, 6x6 e 7x7 (sem 3x3), folhas A4 a A2,
ordenacao de cantos e rejeicao total de falsos positivos.
"""

import json
import subprocess
import unittest
from pathlib import Path

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent


def run_js_detector(rgba_bytes: bytes, width: int, height: int, max_dim: int = 640) -> dict:
    js_code = f"""
    require('./edit/src/lib/aruco_dictionaries.js');
    require('./edit/src/lib/aruco_universal.js');
    const fs = require('fs');
    const buf = fs.readFileSync(0);
    const det = new global.ArUcoUniversalDetector();
    const markers = det.detectMarkersDownsampled(new Uint8Array(buf), {width}, {height}, {max_dim});
    const corners = global.ArUcoUniversalDetector.identifyTargetCorners(markers, {width}, {height});
    console.log(JSON.stringify({{ markers, corners, cascade: det.searchCascade }}));
    """
    proc = subprocess.run(
        ["node", "-e", js_code],
        cwd=BASE_DIR,
        input=rgba_bytes,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Erro Node.js: {proc.stderr.decode('utf-8', errors='replace')}")
    return json.loads(proc.stdout.decode("utf-8"))


class TestArucoUniversal(unittest.TestCase):
    def test_dictionaries_json_integrity(self):
        json_path = BASE_DIR / "edit" / "tools" / "aruco_dictionaries_all.json"
        self.assertTrue(json_path.exists(), "Arquivo aruco_dictionaries_all.json deve existir")
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        self.assertNotIn("DICT_3X3_50", data, "DICT_3X3_50 deve estar removido para evitar falsos positivos")
        expected_keys = ["DICT_4X4_250", "DICT_5X5_250", "DICT_6X6_250", "DICT_7X7_250"]
        for k in expected_keys:
            self.assertIn(k, data)
            self.assertEqual(len(data[k]["markers"]), 250)

    def test_node_universal_cascade_without_3x3(self):
        js_code = """
        require('./edit/src/lib/aruco_dictionaries.js');
        require('./edit/src/lib/aruco_universal.js');
        const det = new global.ArUcoUniversalDetector();
        console.log(JSON.stringify({ cascade: det.searchCascade }));
        """
        proc = subprocess.run(["node", "-e", js_code], cwd=BASE_DIR, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Erro no Node: {proc.stderr}")
        res = json.loads(proc.stdout)
        self.assertEqual(
            res["cascade"],
            ["DICT_4X4_250", "DICT_5X5_250", "DICT_6X6_250", "DICT_7X7_250"],
        )

    def test_parity_4x4_to_7x7_sheets_a4_to_a2(self):
        """Gera folhas sinteticas A4, A3 e A2 em perspectiva para 4x4, 5x5, 6x6 e 7x7 e valida paridade com cv2.aruco."""
        configs = [
            ("DICT_4X4_250", cv2.aruco.DICT_4X4_250, [120, 121, 122, 123], 84, "A4"),
            ("DICT_5X5_250", cv2.aruco.DICT_5X5_250, [10, 25, 42, 77], 68, "A3"),
            ("DICT_6X6_250", cv2.aruco.DICT_6X6_250, [5, 60, 115, 200], 56, "A2"),
            ("DICT_7X7_250", cv2.aruco.DICT_7X7_250, [3, 44, 89, 150], 56, "A2"),
        ]
        frame_w, frame_h = 1280, 720

        for dict_name, cv_const, ids, marker_px, paper_size in configs:
            with self.subTest(dictionary=dict_name, paper=paper_size):
                aruco_dict = cv2.aruco.getPredefinedDictionary(cv_const)
                sheet_h = 640
                sheet_w = int(round(sheet_h / np.sqrt(2)))
                sheet = np.full((sheet_h, sheet_w, 3), 248, dtype=np.uint8)

                margin = 24
                positions = [
                    (margin, margin),
                    (sheet_w - margin - marker_px, margin),
                    (sheet_w - margin - marker_px, sheet_h - margin - marker_px),
                    (margin, sheet_h - margin - marker_px),
                ]
                for idx, (mid, (px, py)) in enumerate(zip(ids, positions)):
                    m_img = cv2.aruco.generateImageMarker(aruco_dict, mid, marker_px)
                    m_rot = np.rot90(m_img, -idx)
                    sheet[py:py + marker_px, px:px + marker_px] = cv2.cvtColor(m_rot, cv2.COLOR_GRAY2BGR)

                # Projeta folha na cena 1280x720 com leve inclinacao de perspectiva
                scene = np.full((frame_h, frame_w, 3), 65, dtype=np.uint8)
                src = np.float32([[0, 0], [sheet_w, 0], [sheet_w, sheet_h], [0, sheet_h]])
                ox = (frame_w - sheet_w) // 2
                oy = (frame_h - sheet_h) // 2
                dst = np.float32([
                    [ox + 18, oy + 10],
                    [ox + sheet_w - 14, oy + 18],
                    [ox + sheet_w + 10, oy + sheet_h - 12],
                    [ox - 12, oy + sheet_h - 8],
                ])
                M = cv2.getPerspectiveTransform(src, dst)
                warped = cv2.warpPerspective(sheet, M, (frame_w, frame_h), borderValue=(65, 65, 65))
                mask = cv2.warpPerspective(np.full((sheet_h, sheet_w), 255, dtype=np.uint8), M, (frame_w, frame_h)) > 0
                scene[mask] = warped[mask]

                # Valida com cv2.aruco
                cv_det = cv2.aruco.ArucoDetector(aruco_dict, cv2.aruco.DetectorParameters())
                _, cv_ids, _ = cv_det.detectMarkers(scene)
                self.assertIsNotNone(cv_ids, f"cv2.aruco falhou em {dict_name}")
                self.assertEqual(sorted(cv_ids.flatten().tolist()), sorted(ids))

                # Valida com JS ArUcoUniversalDetector
                rgba = cv2.cvtColor(scene, cv2.COLOR_BGR2RGBA).tobytes()
                js_res = run_js_detector(rgba, frame_w, frame_h, 640)
                js_ids = sorted(m["id"] for m in js_res["markers"])
                self.assertEqual(js_ids, sorted(ids), f"JS falhou em {dict_name} ({paper_size})")
                self.assertTrue(all(m["dictionary"] == dict_name for m in js_res["markers"]))
                self.assertIsNotNone(js_res["corners"], f"Quarteto nao validado em {dict_name}")

    def test_zero_false_positives_on_non_aruco_scenes(self):
        """Cenas sem marcadores ArUco (texto, formas, ruido, quadrados sem borda branca) devem retornar 0 marcadores."""
        img = np.full((720, 1280, 3), 238, dtype=np.uint8)
        for i in range(12):
            cv2.rectangle(img, (40 + i * 95, 50), (100 + i * 95, 110), (0, 0, 0), -1)
            cv2.rectangle(img, (40 + i * 95, 140), (105 + i * 95, 205), (15, 15, 15), 5)
        cv2.putText(img, "TARGET TEST 123 IPSC A4 A2", (100, 350), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 0), 4)
        cv2.circle(img, (640, 520), 120, (0, 0, 0), 6)
        cv2.rectangle(img, (950, 420), (1120, 590), (20, 20, 20), -1)
        cv2.rectangle(img, (980, 450), (1090, 560), (240, 240, 240), -1)

        rng = np.random.default_rng(123)
        noise = rng.integers(-18, 19, size=img.shape, dtype=np.int16)
        noisy = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)

        rgba = cv2.cvtColor(noisy, cv2.COLOR_BGR2RGBA).tobytes()
        js_res = run_js_detector(rgba, 1280, 720, 640)
        self.assertEqual(len(js_res["markers"]), 0, f"Falso positivo detectado: {js_res['markers']}")
        self.assertIsNone(js_res["corners"])

    def test_mantis_high_id_target_parity(self):
        """Valida alvo Mantis com IDs > 50 (136..139) antes nao suportados."""
        target_path = BASE_DIR / "lib" / "Mantis-Target-Analysis-main" / "images" / "Mantis_Laser_Academy_ExpansionPackTargets_2026-06-28_008.png"
        if not target_path.exists():
            self.skipTest("Imagem Mantis expansion pack nao disponivel")

        im = cv2.imread(str(target_path))
        h, w = im.shape[:2]
        if max(h, w) > 1280:
            s = 1280 / max(h, w)
            im = cv2.resize(im, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        h, w = im.shape[:2]

        js_res = run_js_detector(cv2.cvtColor(im, cv2.COLOR_BGR2RGBA).tobytes(), w, h, 640)
        self.assertEqual(sorted(m["id"] for m in js_res["markers"]), [136, 137, 138, 139])
        corners = js_res["corners"]
        self.assertIsNotNone(corners)
        self.assertIn("outerCorners", corners)
        # Cantos externos devem abranger area estritamente maior que os centros
        self.assertLess(corners["outerCorners"]["tl"]["x"], corners["tl"]["x"])
        self.assertLess(corners["outerCorners"]["tl"]["y"], corners["tl"]["y"])
        self.assertGreater(corners["outerCorners"]["br"]["x"], corners["br"]["x"])
        self.assertGreater(corners["outerCorners"]["br"]["y"], corners["br"]["y"])


if __name__ == "__main__":
    unittest.main()
