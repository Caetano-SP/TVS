#!/usr/bin/env python3
"""
Gerador de Alvo Imprimível em Folha A4 com Marcadores ArUco DICT_4X4_50 e QR Code de Calibração
Produz SVG vetorial de alta precisão (210mm x 297mm) pronto para impressão sem perda de qualidade.
Atende à Etapa 1 do Roteiro e à Seção 3 de Regras/04_arquitetura_backend_e_camera.md.
"""

import base64
import json
from pathlib import Path
import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_SVG = BASE_DIR / "src" / "alvo_a4_imprimir.svg"

def gerar_marcador_base64_png(marker_id, dict_type=cv2.aruco.DICT_4X4_50, size_px=220):
    aruco_dict = cv2.aruco.getPredefinedDictionary(dict_type)
    marker_img = cv2.aruco.generateImageMarker(aruco_dict, marker_id, size_px)
    success, encoded_img = cv2.imencode(".png", marker_img)
    if not success:
        raise RuntimeError(f"Erro ao codificar marcador ArUco ID {marker_id}")
    return base64.b64encode(encoded_img).decode("ascii")

def gerar_qrcode_base64_png(payload_str, size_px=240):
    enc = cv2.QRCodeEncoder.create()
    qr_mat = enc.encode(payload_str)
    # Adiciona quiet zone (margem de 4 módulos)
    qr_bordered = cv2.copyMakeBorder(qr_mat, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
    qr_large = cv2.resize(qr_bordered, (size_px, size_px), interpolation=cv2.INTER_NEAREST)
    success, encoded_img = cv2.imencode(".png", qr_large)
    if not success:
        raise RuntimeError("Erro ao codificar QR Code de calibração")
    return base64.b64encode(encoded_img).decode("ascii")

def gerar_alvo_a4_svg(ids=(0, 1, 2, 3), destino_svg=OUTPUT_SVG):
    """
    Gera folha A4 (210mm x 297mm) com:
    - 4 Marcadores ArUco nos cantos (28mm x 28mm cada) para calibração homográfica de perspectiva
    - QR Code de Calibração Inteligente contendo metadados JSON do alvo e escala física
    - Zonas de pontuação concêntricas balísticas (X, 10, 8, 7, 6, 5)
    - Régua de calibração física milimétrica de 50 mm
    """
    w_mm = 210.0
    h_mm = 297.0
    
    margin = 14.0 # 14 mm da borda do papel
    m_size = 28.0 # Marcador 28x28 mm
    
    # 1. Marcadores ArUco 4x4 (IDs 0, 1, 2, 3)
    b64_tl = gerar_marcador_base64_png(ids[0])
    b64_tr = gerar_marcador_base64_png(ids[1])
    b64_br = gerar_marcador_base64_png(ids[2])
    b64_bl = gerar_marcador_base64_png(ids[3])
    
    pos_tl = (margin, margin)
    pos_tr = (w_mm - margin - m_size, margin)
    pos_br = (w_mm - margin - m_size, h_mm - margin - m_size)
    pos_bl = (margin, h_mm - margin - m_size)
    
    # 2. Metadados do Alvo codificados no QR Code
    target_metadata = {
        "id": "VIRTU_A4_01",
        "name": "Smart Target A4 (Oficial)",
        "w_mm": 210,
        "h_mm": 297,
        "aruco_dict": "4X4_50",
        "aruco_corners": list(ids),
        "scale_mm_per_px": 0.35, # 210mm / 600px
        "center_mm": [105.0, 148.5],
        "zones": [
            {"score": 10, "r_mm": 14.0},
            {"score": 8, "r_mm": 26.0},
            {"score": 7, "r_mm": 39.0},
            {"score": 6, "r_mm": 52.0},
            {"score": 5, "r_mm": 65.0}
        ]
    }
    payload_json = json.dumps(target_metadata, separators=(',', ':'))
    b64_qr = gerar_qrcode_base64_png(payload_json)
    
    centro_x = w_mm / 2.0
    centro_y = h_mm / 2.0
    
    # Posição do QR Code (canto inferior esquerdo ao lado do ArUco BL)
    qr_size = 22.0
    qr_x = pos_bl[0] + m_size + 5.0
    qr_y = h_mm - margin - qr_size
    
    svg_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" 
     xmlns:xlink="http://www.w3.org/1999/xlink" 
     width="{w_mm}mm" height="{h_mm}mm" 
     viewBox="0 0 {w_mm} {h_mm}">
  <style>
    .bg {{ fill: #ffffff; }}
    .border-line {{ stroke: #cccccc; stroke-width: 0.3; stroke-dasharray: 2,2; fill: none; }}
    .ring {{ fill: none; stroke: #111111; stroke-width: 0.5; }}
    .ring-thick {{ fill: none; stroke: #111111; stroke-width: 1.0; }}
    .bullseye {{ fill: #111111; }}
    .center-cross {{ stroke: #ffffff; stroke-width: 0.4; }}
    .text-num {{ font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 4px; font-weight: bold; fill: #222222; text-anchor: middle; }}
    .text-title {{ font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 4.8px; font-weight: bold; fill: #111111; text-anchor: middle; letter-spacing: 1.2px; }}
    .text-sub {{ font-family: 'Courier New', monospace; font-size: 2.6px; fill: #555555; text-anchor: middle; }}
    .text-corner {{ font-family: 'Courier New', monospace; font-size: 2.5px; font-weight: bold; fill: #000000; text-anchor: middle; }}
    .text-qr {{ font-family: 'Courier New', monospace; font-size: 2.0px; font-weight: bold; fill: #111111; text-anchor: middle; }}
    .ruler-line {{ stroke: #111111; stroke-width: 0.6; }}
    .ruler-tick {{ stroke: #111111; stroke-width: 0.4; }}
    .ruler-text {{ font-family: 'Courier New', monospace; font-size: 2.2px; fill: #111111; text-anchor: middle; }}
  </style>

  <!-- Fundo Branco Fosco para Impressao -->
  <rect class="bg" width="{w_mm}" height="{h_mm}" />

  <!-- Margem de Seguranca da Impressora (5mm) -->
  <rect class="border-line" x="5" y="5" width="{w_mm - 10}" height="{h_mm - 10}" />

  <!-- Marcadores ArUco nos 4 Cantos (DICT_4X4_50) para Calibracao Homografica -->
  <!-- Top-Left (ID {ids[0]}) -->
  <image x="{pos_tl[0]}" y="{pos_tl[1]}" width="{m_size}" height="{m_size}" xlink:href="data:image/png;base64,{b64_tl}" />
  <text class="text-corner" x="{pos_tl[0] + m_size/2}" y="{pos_tl[1] + m_size + 3.5}">ARUCO ID {ids[0]} (TL)</text>

  <!-- Top-Right (ID {ids[1]}) -->
  <image x="{pos_tr[0]}" y="{pos_tr[1]}" width="{m_size}" height="{m_size}" xlink:href="data:image/png;base64,{b64_tr}" />
  <text class="text-corner" x="{pos_tr[0] + m_size/2}" y="{pos_tr[1] + m_size + 3.5}">ARUCO ID {ids[1]} (TR)</text>

  <!-- Bottom-Right (ID {ids[2]}) -->
  <image x="{pos_br[0]}" y="{pos_br[1]}" width="{m_size}" height="{m_size}" xlink:href="data:image/png;base64,{b64_br}" />
  <text class="text-corner" x="{pos_br[0] + m_size/2}" y="{pos_br[1] - 1.5}">ARUCO ID {ids[2]} (BR)</text>

  <!-- Bottom-Left (ID {ids[3]}) -->
  <image x="{pos_bl[0]}" y="{pos_bl[1]}" width="{m_size}" height="{m_size}" xlink:href="data:image/png;base64,{b64_bl}" />
  <text class="text-corner" x="{pos_bl[0] + m_size/2}" y="{pos_bl[1] - 1.5}">ARUCO ID {ids[3]} (BL)</text>

  <!-- Cabecalho Tecnico do Alvo -->
  <text class="text-title" x="{centro_x}" y="21">TARGETS VIRTU — SMART TARGET A4</text>
  <text class="text-sub" x="{centro_x}" y="25.5">CALIBRACAO HOMOGRAFICA 2D + IDENTIFICACAO QR CODE</text>

  <!-- QR Code de Calibracao e Perfil Inteligente -->
  <image x="{qr_x}" y="{qr_y}" width="{qr_size}" height="{qr_size}" xlink:href="data:image/png;base64,{b64_qr}" />
  <text class="text-qr" x="{qr_x + qr_size/2}" y="{qr_y + qr_size + 3.0}">QR CALIBRACAO</text>

  <!-- Aneis Concentricos de Pontuacao Balistica -->
  <!-- Zona 5: Raio 65mm -->
  <circle class="ring" cx="{centro_x}" cy="{centro_y}" r="65" />
  <text class="text-num" x="{centro_x}" y="{centro_y - 60}">5</text>
  <text class="text-num" x="{centro_x}" y="{centro_y + 63}">5</text>

  <!-- Zona 6: Raio 52mm -->
  <circle class="ring" cx="{centro_x}" cy="{centro_y}" r="52" />
  <text class="text-num" x="{centro_x}" y="{centro_y - 47}">6</text>
  <text class="text-num" x="{centro_x}" y="{centro_y + 50}">6</text>

  <!-- Zona 7: Raio 39mm -->
  <circle class="ring" cx="{centro_x}" cy="{centro_y}" r="39" />
  <text class="text-num" x="{centro_x}" y="{centro_y - 34}">7</text>
  <text class="text-num" x="{centro_x}" y="{centro_y + 37}">7</text>

  <!-- Zona 8: Raio 26mm -->
  <circle class="ring-thick" cx="{centro_x}" cy="{centro_y}" r="26" />
  <text class="text-num" x="{centro_x}" y="{centro_y - 21}">8</text>
  <text class="text-num" x="{centro_x}" y="{centro_y + 24}">8</text>

  <!-- Zona 9 / 10 / X (Centro Preto - Bullseye) -->
  <circle class="bullseye" cx="{centro_x}" cy="{centro_y}" r="14" />
  
  <!-- Anel 10 (Linha branca interna) -->
  <circle cx="{centro_x}" cy="{centro_y}" r="8" fill="none" stroke="#ffffff" stroke-width="0.3" stroke-dasharray="1,1" />

  <!-- Cruz Central Milimetrica (Branca) -->
  <line class="center-cross" x1="{centro_x - 3.5}" y1="{centro_y}" x2="{centro_x + 3.5}" y2="{centro_y}" />
  <line class="center-cross" x1="{centro_x}" y1="{centro_y - 3.5}" x2="{centro_x}" y2="{centro_y + 3.5}" />

  <!-- Regua de Afericao de Escala Fisica (50 mm) -->
  <g transform="translate({centro_x - 10}, {h_mm - 19})">
    <line class="ruler-line" x1="0" y1="0" x2="50" y2="0" />
    <line class="ruler-tick" x1="0" y1="-3" x2="0" y2="3" />
    <line class="ruler-tick" x1="10" y1="-2" x2="10" y2="2" />
    <line class="ruler-tick" x1="20" y1="-2" x2="20" y2="2" />
    <line class="ruler-tick" x1="30" y1="-2" x2="30" y2="2" />
    <line class="ruler-tick" x1="40" y1="-2" x2="40" y2="2" />
    <line class="ruler-tick" x1="50" y1="-3" x2="50" y2="3" />
    <text class="ruler-text" x="25" y="5">ESCALA REAL: 50 MM (AFERICAO)</text>
  </g>
</svg>
"""
    destino_svg.parent.mkdir(parents=True, exist_ok=True)
    with open(destino_svg, "w", encoding="utf-8") as f:
        f.write(svg_content)
    print(f"[SUCESSO] Alvo A4 vetorial com ArUco e QR Code gerado em: {destino_svg}")

if __name__ == "__main__":
    gerar_alvo_a4_svg()
