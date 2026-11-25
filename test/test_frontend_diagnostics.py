#!/usr/bin/env python3
"""
Suíte de Diagnóstico Automatizado do Front-end (TARGETS_VIRTU)
==============================================================

Etapa 1 do plano `Regras/11_plano_diagnostico_e_melhoria_frontend.md`.

Audita `edit/src/index.html`, `edit/src/style.css`, `edit/src/app.js`,
`edit/src/ballistics_echarts.js` e os Web Workers em 5 frentes:

  1. Auditoria das Regras Proibitivas (`Regras/05_style_css.md` e `Regras/06_regras_de_visual.md`);
  2. Integridade DOM <-> JavaScript (IDs órfãos, módulos ocultos, painéis presos em `.sr-only`);
  3. Invariantes geométricas (resolução, espelhamento, zoom digital e homografia);
  4. Benchmark de frametime/alocação (`warpTargetView`) e ciclo de vida das rodadas;
  5. Contrato JSON Front-end <-> Back-end (`POST/GET /api/sessions`).

Convenção dos resultados
------------------------
* Testes normais: invariantes que DEVEM permanecer verdadeiras (guardas de regressão).
* Testes `@unittest.expectedFailure`: defeitos CONHECIDOS e documentados no diagnóstico
  (Seção 1 do plano), vinculados a uma Entrega da Seção 3. Hoje eles falham de propósito
  ("expected failure"). Quando a Entrega correspondente for implementada, o unittest
  acusará "unexpected success" (suíte vermelha) - sinal para remover o decorador e
  promover o teste a guarda de regressão.

Um relatório consolidado é gravado em `test/output/frontend_diagnostics_report.json`.

Execução:
    .venv/bin/python3 -m unittest test.test_frontend_diagnostics -v
    .venv/bin/python3 -m unittest discover -s test -v
"""

import http.client
import json
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "edit" / "src"
TOOLS_DIR = BASE_DIR / "edit" / "tools"
OUTPUT_DIR = BASE_DIR / "test" / "output"
REPORT_PATH = OUTPUT_DIR / "frontend_diagnostics_report.json"

INDEX_HTML = SRC_DIR / "index.html"
STYLE_CSS = SRC_DIR / "style.css"
APP_JS = SRC_DIR / "app.js"
BALLISTICS_JS = SRC_DIR / "ballistics_echarts.js"
ARUCO_JS = SRC_DIR / "lib" / "aruco_universal.js"
WORKER_FILES = [SRC_DIR / "workers" / "aruco-worker.js", SRC_DIR / "workers" / "laser-worker.js"]
UI_SOURCES = [INDEX_HTML, STYLE_CSS, APP_JS, BALLISTICS_JS, *WORKER_FILES]

sys.path.insert(0, str(TOOLS_DIR))
import servidor_dev  # noqa: E402

NODE_BIN = shutil.which("node")
requires_node = unittest.skipIf(NODE_BIN is None, "Node.js não encontrado no PATH")

# IDs referenciados no JS mas ausentes no DOM (diagnóstico 1.6 / 1.8). Qualquer órfão NOVO quebra a suíte.
KNOWN_ORPHAN_IDS = {"btn-manual-calibrate", "btn-beep-test"}

TARGET_W, TARGET_H = 600, 800
SUBPIXEL_TOLERANCE_PX = 1.5

REPORT = {
    "generated_at": None,
    "plan": "Regras/11_plano_diagnostico_e_melhoria_frontend.md",
    "sections": {},
}


def record(section: str, key: str, value) -> None:
    REPORT["sections"].setdefault(section, {})[key] = value


def tearDownModule():  # noqa: N802 (API do unittest)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT["generated_at"] = datetime.now().isoformat(timespec="seconds")
    REPORT_PATH.write_text(json.dumps(REPORT, indent=2, ensure_ascii=False), encoding="utf-8")


# =====================================================================
# Utilitários de leitura / extração de código-fonte
# =====================================================================
_TEXT_CACHE: dict = {}


def read(path: Path) -> str:
    if path not in _TEXT_CACHE:
        _TEXT_CACHE[path] = path.read_text(encoding="utf-8")
    return _TEXT_CACHE[path]


def line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def strip_css_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def match_brace(src: str, open_idx: int, open_ch: str = "{", close_ch: str = "}") -> int:
    """Retorna o índice do delimitador que fecha `src[open_idx]`, ignorando strings e comentários."""
    depth = 0
    i = open_idx
    n = len(src)
    while i < n:
        c = src[i]
        if c in "'\"`":
            quote = c
            i += 1
            while i < n and src[i] != quote:
                if src[i] == "\\":
                    i += 1
                i += 1
        elif src.startswith("//", i):
            nl = src.find("\n", i)
            i = n if nl == -1 else nl
        elif src.startswith("/*", i):
            end = src.find("*/", i + 2)
            i = n if end == -1 else end + 1
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError(f"Delimitador '{open_ch}' sem fechamento a partir de {open_idx}")


def extract_js_function(src: str, name: str) -> str:
    """Extrai o código-fonte completo de `function name(...) {...}` (ou `async function`)."""
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    if not m:
        raise AssertionError(f"Função '{name}' não encontrada")
    params_close = match_brace(src, m.end() - 1, "(", ")")
    body_open = src.index("{", params_close)
    return src[m.start(): match_brace(src, body_open) + 1]


def extract_listener(src: str, prefix: str) -> str:
    """Extrai `prefix (e) => { ... });` de um addEventListener de nível superior."""
    start = src.index(prefix)
    body_open = src.index("{", start)
    return src[start: match_brace(src, body_open) + 1] + ");"


def extract_css_block(css: str, header_regex: str) -> str:
    m = re.search(header_regex, css)
    if not m:
        return ""
    body_open = css.index("{", m.start())
    return css[body_open + 1: match_brace(css, body_open)]


def run_node(script: str, payload: dict) -> dict:
    """Executa `script` no Node.js recebendo `payload` (JSON) via stdin; retorna o JSON da última linha."""
    proc = subprocess.run(
        [NODE_BIN, "-e", script],
        cwd=BASE_DIR,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Erro Node.js:\n{proc.stderr}")
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    return json.loads(lines[-1])


def html_ids(html: str) -> set:
    return set(re.findall(r'\bid\s*=\s*"([^"]+)"', html))


def js_referenced_ids(js: str) -> set:
    ids = set(re.findall(r"getElementById\(\s*['\"]([^'\"]+)['\"]\s*\)", js))
    ids |= set(re.findall(r"querySelector(?:All)?\(\s*['\"]#([A-Za-z0-9_-]+)", js))
    return ids


# =====================================================================
# 1. Auditoria das Regras Proibitivas (Regras/05 e Regras/06)
# =====================================================================
class TestVisualRulesAudit(unittest.TestCase):
    """Guardas de regressão das 15 Regras Proibitivas aplicáveis estaticamente."""

    def _scan(self, pattern: str, files=None, flags=0, css_strip=True) -> list:
        hits = []
        for path in files or UI_SOURCES:
            text = read(path)
            if css_strip and path.suffix == ".css":
                text = strip_css_comments(text)
            for m in re.finditer(pattern, text, flags):
                hits.append(f"{path.name}:{line_of(text, m.start())}: {m.group(0).strip()[:80]}")
        return hits

    def test_zero_gradientes(self):
        hits = self._scan(r"(?:repeating-)?(?:linear|radial|conic)-gradient\s*\(|echarts\.graphic\.(?:Linear|Radial)Gradient")
        record("1_regras_visuais", "gradientes", hits)
        self.assertEqual(hits, [], "Gradientes proibidos (Regras/06 #1 e #11)")

    def test_border_radius_maximo_4px(self):
        violations = []
        for path in UI_SOURCES:
            text = read(path)
            if path.suffix == ".css":
                text = strip_css_comments(text)
            for m in re.finditer(r"border-radius\s*:\s*([^;\"}]+)", text):
                for token in m.group(1).replace("/", " ").split():
                    num = re.match(r"(-?[\d.]+)(px|%|em|rem|vh|vw)?$", token)
                    if not num:
                        continue
                    value, unit = float(num.group(1)), (num.group(2) or "px")
                    if (unit == "px" and value > 4) or (unit != "px" and value > 0):
                        violations.append(f"{path.name}:{line_of(text, m.start())}: {m.group(0).strip()}")
            for m in re.finditer(r"borderRadius\s*[:=]\s*(\[[^\]]*\]|[\d.]+)", text):
                values = [float(v) for v in re.findall(r"[\d.]+", m.group(1))]
                if any(v > 4 for v in values):
                    violations.append(f"{path.name}:{line_of(text, m.start())}: {m.group(0).strip()}")
        record("1_regras_visuais", "border_radius_acima_4px", violations)
        self.assertEqual(violations, [], "Cantos acima de 4px / pílulas (Regras/06 #2 e #4)")

    def test_zero_tipografia_inter(self):
        hits = self._scan(r"font-family[^;{}]*\bInter\b|fontFamily\s*:\s*['\"][^'\"]*\bInter\b|['\"`][^'\"`]*\d+px\s+['\"]?Inter\b")
        record("1_regras_visuais", "tipografia_inter", hits)
        self.assertEqual(hits, [], "Tipografia Inter proibida (Regras/06 #3)")

    def test_zero_backdrop_filter(self):
        hits = self._scan(r"backdrop-filter|backdropFilter")
        record("1_regras_visuais", "backdrop_filter", hits)
        self.assertEqual(hits, [], "Vidro fosco proibido (Regras/06 #7)")

    def test_zero_glow_text_shadow_e_pulse(self):
        hits = self._scan(
            r"text-shadow\s*:\s*(?!none)[^;]+|textShadow\w*\s*:\s*[1-9]|@keyframes|animation[^;:]*:\s*[^;]*pulse"
            r"|filter\s*:\s*drop-shadow|shadowBlur\s*[:=]\s*[1-9]"
        )
        record("1_regras_visuais", "glow_pulse", hits)
        self.assertEqual(hits, [], "Glow / text-shadow / pulso proibidos (Regras/06 #5)")

    def test_sombras_discretas(self):
        """Sombras permitidas apenas discretas (blur <= 12px) para evitar 'sombras em excesso' (#14)."""
        css = strip_css_comments(read(STYLE_CSS))
        declarations = re.findall(r"(?:box-shadow|--card-shadow)\s*:\s*([^;]+);", css)
        heavy = []
        for decl in declarations:
            if decl.strip().startswith("var(") or decl.strip() == "none":
                continue
            px = [float(v) for v in re.findall(r"(-?[\d.]+)px", decl)]
            if len(px) >= 3 and px[2] > 12:
                heavy.append(decl.strip())
        record("1_regras_visuais", "sombras", {"declaracoes": len(declarations), "pesadas": heavy})
        self.assertEqual(heavy, [])

    def test_zero_font_weight_800_900(self):
        hits = self._scan(
            r"font-weight\s*:\s*(?:800|900|bolder)\b|fontWeight\s*:\s*['\"]?(?:800|900|bolder)\b"
            r"|['\"`](?:800|900)\s+\d+px"
        )
        record("1_regras_visuais", "font_weight_800_900", hits)
        self.assertEqual(hits, [], "Títulos 800/900 proibidos (Regras/06 #8)")

    def test_icones_svg_stroke_minimo_2px(self):
        thin = []
        for path in UI_SOURCES:
            text = read(path)
            for m in re.finditer(r"stroke-width\s*[=:]\s*[\"']?([\d.]+)", text):
                if float(m.group(1)) < 2:
                    thin.append(f"{path.name}:{line_of(text, m.start())}: {m.group(0)}")
        svg_count = sum(read(p).count("<svg") for p in UI_SOURCES)
        record("1_regras_visuais", "svg_stroke", {"svgs": svg_count, "finos": thin})
        self.assertGreater(svg_count, 0)
        self.assertEqual(thin, [], "Ícones com contorno < 2px (Regras/06 #10)")

    def test_zero_emojis(self):
        emoji_ranges = [(0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF), (0xFE0F, 0xFE0F), (0x1F900, 0x1F9FF)]
        hits = []
        for path in UI_SOURCES:
            for n, line in enumerate(read(path).splitlines(), 1):
                for ch in line:
                    if any(lo <= ord(ch) <= hi for lo, hi in emoji_ranges):
                        hits.append(f"{path.name}:{n}: U+{ord(ch):04X}")
        record("1_regras_visuais", "emojis", hits)
        self.assertEqual(hits, [], "Emojis proibidos no código de interface")


# =====================================================================
# 2. Integridade DOM <-> JavaScript (diagnósticos 1.3, 1.5, 1.6, 1.8)
# =====================================================================
class TestDomIntegrity(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.html = read(INDEX_HTML)
        cls.css = strip_css_comments(read(STYLE_CSS))
        cls.app = read(APP_JS)
        cls.dom_ids = html_ids(cls.html)
        cls.js_ids = js_referenced_ids(cls.app) | js_referenced_ids(read(BALLISTICS_JS))
        cls.missing = sorted(cls.js_ids - cls.dom_ids)
        record("2_integridade_dom", "ids_no_dom", len(cls.dom_ids))
        record("2_integridade_dom", "ids_referenciados_js", len(cls.js_ids))
        record("2_integridade_dom", "ids_orfaos", cls.missing)

    def test_ids_duplicados_no_dom(self):
        all_ids = re.findall(r'\bid\s*=\s*"([^"]+)"', self.html)
        dups = sorted({i for i in all_ids if all_ids.count(i) > 1})
        self.assertEqual(dups, [])

    def test_nenhum_novo_id_orfao(self):
        """Guarda de regressão: só os órfãos já diagnosticados são tolerados."""
        new_orphans = sorted(set(self.missing) - KNOWN_ORPHAN_IDS)
        self.assertEqual(new_orphans, [], f"Novos IDs referenciados no JS e ausentes no DOM: {new_orphans}")

    def test_abas_obrigatorias_existem(self):
        """Regras/05: 4 abas dedicadas com data-tab e navegação mobile + desktop."""
        tabs = re.findall(r'class="[^"]*\btab-card\b[^"]*"[^>]*data-tab="([^"]+)"', self.html)
        self.assertEqual(sorted(tabs), sorted(["camera", "alvo", "estatisticas", "ajustes"]))
        for target in ("camera", "alvo", "estatisticas", "ajustes"):
            self.assertRegex(self.html, rf'class="nav-item[^"]*"\s+data-target="{target}"')
            self.assertRegex(self.html, rf'class="nav-desktop-btn[^"]*"\s+data-target="{target}"')

    @unittest.expectedFailure
    def test_defeito_1_6_1_8_zero_ids_orfaos(self):
        """[Entrega 1 / 2] `#btn-manual-calibrate` e `#btn-beep-test` não existem no DOM."""
        self.assertEqual(self.missing, [])

    @unittest.expectedFailure
    def test_defeito_1_6_botao_calibracao_manual_no_dom(self):
        """[Entrega 1] Fallback manual de 4 pontos inacessível ao usuário."""
        self.assertIn("btn-manual-calibrate", self.dom_ids)

    @unittest.expectedFailure
    def test_defeito_1_3_zero_modulos_bloqueados(self):
        """[Entrega 3] Aba 3, Shot Timer, KPIs e Perfil/Zonas presos em `.is-hidden-module`."""
        hidden = []
        for m in re.finditer(r"<(\w+)([^>]*\bclass=\"[^\"]*\bis-hidden-module\b[^\"]*\"[^>]*)>", self.html):
            attrs = m.group(2)
            ident = re.search(r'\bid="([^"]+)"', attrs) or re.search(r'data-target="([^"]+)"', attrs) \
                or re.search(r'class="([^"]+)"', attrs)
            hidden.append(f"<{m.group(1)}> {ident.group(1)} (linha {line_of(self.html, m.start())})")
        record("2_integridade_dom", "modulos_ocultos_is_hidden_module", hidden)
        self.assertEqual(hidden, [])

    @unittest.expectedFailure
    def test_defeito_1_3_layout_desktop_390px_1fr(self):
        """[Entrega 3] Regras/05 exige grid Desktop `390px 1fr` (>= 1080px)."""
        block = extract_css_block(self.css, r"@media\s*\(\s*min-width\s*:\s*1080px\s*\)")
        cols = re.search(r"grid-template-columns\s*:\s*([^;]+);", block)
        record("2_integridade_dom", "desktop_grid_template_columns", cols.group(1).strip() if cols else None)
        self.assertIsNotNone(cols)
        self.assertRegex(cols.group(1), r"^\s*390px\s+1fr\s*$")

    @unittest.expectedFailure
    def test_defeito_1_3_desktop_respeita_aba_ativa(self):
        """[Entrega 3] No Desktop, `.tab-card {display:block}` exibe todas as abas e anula a navegação."""
        block = extract_css_block(self.css, r"@media\s*\(\s*min-width\s*:\s*1080px\s*\)")
        forced = []
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", block):
            selectors = [s.strip() for s in m.group(1).split(",")]
            if re.search(r"display\s*:\s*block", m.group(2)):
                forced += [s for s in selectors if re.search(r"\.tab-card(?![\w-])(?!\.active-tab)", s)]
        record("2_integridade_dom", "desktop_tab_card_forcado_block", forced)
        self.assertEqual(forced, [])

    @unittest.expectedFailure
    def test_defeito_1_5_banner_de_erro_da_camera_visivel(self):
        """[Entrega 1] Erros de getUserMedia só vão para `#diagnostics-panel` (classe `.sr-only`)."""
        diag_sr_only = bool(re.search(r'id="diagnostics-panel"\s+class="[^"]*\bsr-only\b', self.html))
        catch_src = extract_js_function(self.app, "startCamera")
        catch_body = catch_src[catch_src.rindex("catch"):]
        record("2_integridade_dom", "diagnostics_panel_sr_only", diag_sr_only)
        record("2_integridade_dom", "camera_error_banner_presente", "camera-error-banner" in self.dom_ids)
        self.assertIn("camera-error-banner", self.dom_ids)
        self.assertRegex(catch_body, r"cameraErrorBanner|camera-error-banner")

    @unittest.expectedFailure
    def test_defeito_1_8_zero_botoes_mortos_sr_only(self):
        """[Entrega 2] Botões legados escondidos em `.sr-only` (#tab-pip, #tab-target, #tab-camera, #btn-flip...)."""
        dead = re.findall(r'<button[^>]*id="([^"]+)"[^>]*class="sr-only"', self.html)
        record("2_integridade_dom", "botoes_mortos_sr_only", dead)
        self.assertEqual(dead, [])


# =====================================================================
# 3. Invariantes Geométricas (diagnósticos 1.4 e 1.5.3)
# =====================================================================
GEOMETRY_SCRIPT = r"""
const fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
require(input.aruco);
const HG = globalThis.Homography;
const TW = 600, TH = 800, cw = 640, ch = 480;
const dst = [{x: 0, y: 0}, {x: TW - 1, y: 0}, {x: TW - 1, y: TH - 1}, {x: 0, y: TH - 1}];
// Quarteto externo levemente rotacionado/perspectivado (folha A4 vista a ~1.5m)
const src = [{x: 118, y: 58}, {x: 522, y: 71}, {x: 541, y: 433}, {x: 97, y: 419}];
const H = HG.getTransform(src, dst);
const Hinv = HG.invertMatrix(H);
const probes = [{x: 300, y: 400}, {x: 60, y: 80}, {x: 540, y: 720}, {x: 450, y: 150}, {x: 150, y: 650}];

function maxErr(camTransform, Huse) {
  let worst = 0;
  for (const P of probes) {
    const cam = HG.projectPoint(Hinv, P);       // ponto físico visto no frame original
    const camNow = camTransform(cam);           // mesmo ponto físico no frame atual
    const q = HG.projectPoint(Huse, camNow);    // projeção usando a homografia em uso
    worst = Math.max(worst, Math.hypot(q.x - P.x, q.y - P.y));
  }
  return worst;
}

const identity = p => p;
const scaleRes = p => ({x: p.x * (1280 / cw), y: p.y * (720 / ch)});   // 640x480 -> 1280x720
const mirror = p => ({x: cw - p.x, y: p.y});                             // drawCameraView com isFlipped
const zoom2x = p => ({x: (p.x - cw / 2) * 2 + cw / 2, y: (p.y - ch / 2) * 2 + ch / 2});

console.log(JSON.stringify({
  roundtrip: maxErr(identity, H),
  resolutionStale: maxErr(scaleRes, H),
  resolutionRelock: maxErr(scaleRes, HG.getTransform(src.map(scaleRes), dst)),
  flipStale: maxErr(mirror, H),
  flipRelock: maxErr(mirror, HG.getTransform(src.map(mirror), dst)),
  zoomStale: maxErr(zoom2x, H),
  zoomRelock: maxErr(zoom2x, HG.getTransform(src.map(zoom2x), dst))
}));
"""

ROI_SCRIPT = r"""
const fs = require("fs");
const vm = require("vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const fnSrc = input.fns.getDigitalZoomSourceRect + "\n" + input.fns.extractLaserRoiImageData;

function run(cfg) {
  const calls = [];
  const ctx = {
    Math, Number, Uint8ClampedArray,
    video: {videoWidth: cfg.vw, videoHeight: cfg.vh, readyState: 4},
    cameraCanvas: {width: cfg.cw, height: cfg.ch},
    state: {digitalZoom: cfg.dz, isTargetLocked: true, isFlipped: cfg.flip},
    document: {body: {classList: {contains: () => false}}},
    roiCanvas: {width: 0, height: 0},
    roiCtx: {
      save() {}, restore() {}, translate() {}, scale() {},
      drawImage(...a) { calls.push(a); },
      getImageData(x, y, w, h) { return {data: new Uint8ClampedArray(w * h * 4)}; }
    },
    cameraCtx: {getImageData() { throw new Error("caminho de canvas completo inesperado"); }}
  };
  vm.createContext(ctx);
  vm.runInContext(fnSrc, ctx);
  ctx.__c = cfg;
  const img = vm.runInContext("extractLaserRoiImageData(__c.sx, __c.sy, __c.rw, __c.rh, __c.cw, __c.ch)", ctx);
  const r = vm.runInContext("getDigitalZoomSourceRect()", ctx);
  const [, srcX, srcY, srcW, srcH] = calls[0];
  let errX = 0, errY = 0;
  for (let i = 0; i <= cfg.rw; i++) {
    const u = cfg.sx + i;
    const viaRoi = cfg.flip ? srcX + (cfg.rw - i) * srcW / cfg.rw : srcX + i * srcW / cfg.rw;
    const viaFull = cfg.flip ? r.visX + (cfg.cw - u) * r.visW / cfg.cw : r.visX + u * r.visW / cfg.cw;
    errX = Math.max(errX, Math.abs(viaRoi - viaFull) * cfg.cw / r.visW);
  }
  for (let j = 0; j <= cfg.rh; j++) {
    const v = cfg.sy + j;
    const viaRoi = srcY + j * srcH / cfg.rh;
    const viaFull = r.visY + v * r.visH / cfg.ch;
    errY = Math.max(errY, Math.abs(viaRoi - viaFull) * cfg.ch / r.visH);
  }
  return {cfg, errX, errY, bytes: img.data.length, expectedBytes: cfg.rw * cfg.rh * 4};
}

const scenarios = [];
for (const dz of [1.0, 1.5, 2.0, 3.0]) {
  for (const flip of [false, true]) {
    scenarios.push(run({vw: 640, vh: 480, cw: 640, ch: 480, dz, flip, sx: 73, sy: 34, rw: 493, rh: 423}));
    scenarios.push(run({vw: 1280, vh: 720, cw: 1280, ch: 720, dz, flip, sx: 211, sy: 17, rw: 801, rh: 690}));
    // Canvas em fallback de resolução (videoWidth ainda 0 no setup) com aspecto distinto do vídeo
    scenarios.push(run({vw: 1280, vh: 720, cw: 640, ch: 480, dz, flip, sx: 73, sy: 34, rw: 493, rh: 423}));
  }
}
console.log(JSON.stringify({scenarios}));
"""

STATE_GUARDS_SCRIPT = r"""
const fs = require("fs");
const vm = require("vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const H0 = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];

async function main() {
  // (a) applyCameraResolution com alvo travado
  const resCtx = {
    state: {isStreaming: true, isTargetLocked: true, homographyMatrix: H0, resolutionKey: "640x480"},
    cameraSelect: {value: ""},
    calls: {startCamera: 0, unlockTarget: 0},
  };
  resCtx.startCamera = async () => { resCtx.calls.startCamera++; };
  resCtx.unlockTarget = () => { resCtx.calls.unlockTarget++; resCtx.state.isTargetLocked = false; resCtx.state.homographyMatrix = null; };
  vm.createContext(resCtx);
  vm.runInContext(input.fns.applyCameraResolution, resCtx);
  await vm.runInContext('applyCameraResolution("1280x720")', resCtx);

  // (b) Toggle #chk-flip com alvo travado
  const handlers = {};
  const flipCtx = {
    Boolean,
    chkFlip: {addEventListener(ev, fn) { handlers[ev] = fn; }},
    btnFlip: null,
    state: {isFlipped: false, isTargetLocked: true, homographyMatrix: H0},
    calls: {unlockTarget: 0},
  };
  flipCtx.unlockTarget = () => { flipCtx.calls.unlockTarget++; flipCtx.state.isTargetLocked = false; flipCtx.state.homographyMatrix = null; };
  vm.createContext(flipCtx);
  vm.runInContext(input.flipListener, flipCtx);
  handlers.change({target: {checked: true}});

  // (c) applyCameraZoom deve ser ignorado com alvo travado
  const zoomCtx = {
    Math, Number,
    state: {isTargetLocked: true, zoomLevel: 1.0, digitalZoom: 1.0, markerCache: new Map(), currentStream: null},
    calls: {syncZoomUI: 0},
  };
  zoomCtx.syncZoomUI = () => { zoomCtx.calls.syncZoomUI++; };
  vm.createContext(zoomCtx);
  vm.runInContext(input.fns.applyCameraZoom, zoomCtx);
  await vm.runInContext("applyCameraZoom(3)", zoomCtx);

  console.log(JSON.stringify({
    resolution: {calls: resCtx.calls, stillLocked: resCtx.state.isTargetLocked, staleMatrix: resCtx.state.homographyMatrix === H0},
    flip: {calls: flipCtx.calls, isFlipped: flipCtx.state.isFlipped, stillLocked: flipCtx.state.isTargetLocked,
           staleMatrix: flipCtx.state.homographyMatrix === H0},
    zoom: {calls: zoomCtx.calls, zoomLevel: zoomCtx.state.zoomLevel, digitalZoom: zoomCtx.state.digitalZoom}
  }));
}
main().catch(e => { console.error(e); process.exit(1); });
"""


@requires_node
class TestGeometricInvariants(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app = read(APP_JS)
        cls.geo = run_node(GEOMETRY_SCRIPT, {"aruco": str(ARUCO_JS)})
        cls.roi = run_node(ROI_SCRIPT, {"fns": {
            "getDigitalZoomSourceRect": extract_js_function(app, "getDigitalZoomSourceRect"),
            "extractLaserRoiImageData": extract_js_function(app, "extractLaserRoiImageData"),
        }})
        cls.guards = run_node(STATE_GUARDS_SCRIPT, {
            "fns": {
                "applyCameraResolution": extract_js_function(app, "applyCameraResolution"),
                "applyCameraZoom": extract_js_function(app, "applyCameraZoom"),
            },
            "flipListener": extract_listener(app, 'chkFlip?.addEventListener("change"'),
        })
        record("3_geometria", "homografia_erros_px", {k: round(v, 4) for k, v in cls.geo.items()})
        record("3_geometria", "roi_max_erro_px", {
            "x": round(max(s["errX"] for s in cls.roi["scenarios"]), 4),
            "y": round(max(s["errY"] for s in cls.roi["scenarios"]), 4),
            "cenarios": len(cls.roi["scenarios"]),
        })
        record("3_geometria", "guardas_de_estado", cls.guards)

    def test_homografia_roundtrip_subpixel(self):
        self.assertLess(self.geo["roundtrip"], 1e-6)

    def test_relock_apos_resolucao_flip_e_zoom_recupera_subpixel(self):
        """Estratégia de correção (Entrega 1): recalcular H com os cantos novos mantém erro < 1,5 px."""
        for key in ("resolutionRelock", "flipRelock", "zoomRelock"):
            self.assertLess(self.geo[key], SUBPIXEL_TOLERANCE_PX, key)

    def test_homografia_obsoleta_e_insegura(self):
        """Confirma o risco do diagnóstico 1.4/1.5.3: reutilizar H após troca de frame desloca o impacto."""
        for key in ("resolutionStale", "flipStale", "zoomStale"):
            self.assertGreater(self.geo[key], SUBPIXEL_TOLERANCE_PX * 10, key)

    def test_roi_laser_alinhada_ao_canvas_com_zoom_e_flip(self):
        """Diagnóstico 1.4.2: o recorte direto do <video> coincide com o mapeamento de drawCameraView()."""
        for s in self.roi["scenarios"]:
            self.assertLess(s["errX"], SUBPIXEL_TOLERANCE_PX, s["cfg"])
            self.assertLess(s["errY"], SUBPIXEL_TOLERANCE_PX, s["cfg"])
            self.assertEqual(s["bytes"], s["expectedBytes"])

    def test_zoom_bloqueado_com_alvo_travado(self):
        z = self.guards["zoom"]
        self.assertEqual(z["zoomLevel"], 1.0)
        self.assertEqual(z["calls"]["syncZoomUI"], 0)

    def test_start_camera_lista_lentes_apos_get_user_media(self):
        """Dentro de startCamera(), a enumeração ocorre depois de abrir o stream (labels liberados)."""
        start_cam = extract_js_function(read(APP_JS), "startCamera")
        self.assertLess(start_cam.index("_openCameraStream("), start_cam.index("listCameras()"))

    @unittest.expectedFailure
    def test_defeito_1_5_1_lista_de_lentes_somente_apos_autorizacao(self):
        """[Entrega 1] `listCameras()` também é chamado no startup, antes de `getUserMedia()`."""
        app = read(APP_JS)
        outside = app.replace(extract_js_function(app, "startCamera"), "")
        outside = outside.replace(extract_js_function(app, "listCameras"), "")
        lines = [line_of(outside, m.start()) for m in re.finditer(r"\blistCameras\s*\(", outside)]
        record("3_geometria", "listCameras_chamadas_antes_da_autorizacao", len(lines))
        self.assertEqual(lines, [])

    @unittest.expectedFailure
    def test_defeito_1_5_3_troca_de_resolucao_destrava_alvo(self):
        """[Entrega 1] applyCameraResolution() reinicia o stream mantendo a homografia antiga."""
        r = self.guards["resolution"]
        self.assertTrue(r["calls"]["unlockTarget"] > 0 or not r["stillLocked"], r)

    @unittest.expectedFailure
    def test_defeito_1_4_1_espelhamento_destrava_ou_recalcula(self):
        """[Entrega 1] Alternar #chk-flip com alvo travado inverte o eixo X dos impactos."""
        f = self.guards["flip"]
        self.assertTrue(f["isFlipped"])
        self.assertTrue(f["calls"]["unlockTarget"] > 0 or not f["staleMatrix"], f)


# =====================================================================
# 4. Benchmark warpTargetView, Zero-GC e ciclo de vida das rodadas
# =====================================================================
WARP_SCRIPT = r"""
const fs = require("fs");
const vm = require("vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
require(input.aruco);
const HG = globalThis.Homography;
const TW = 600, TH = 800, cw = 640, ch = 480;
const dst = [{x: 0, y: 0}, {x: TW - 1, y: 0}, {x: TW - 1, y: TH - 1}, {x: 0, y: TH - 1}];
const H = HG.getTransform([{x: 118, y: 58}, {x: 522, y: 71}, {x: 541, y: 433}, {x: 97, y: 419}], dst);

const camData = new Uint8ClampedArray(cw * ch * 4);
for (let y = 0; y < ch; y++) for (let x = 0; x < cw; x++) {
  const i = (y * cw + x) * 4;
  camData[i] = x & 255; camData[i + 1] = y & 255; camData[i + 2] = (x >> 8) | ((y >> 8) << 2); camData[i + 3] = 255;
}

function makeCtx(homography) {
  const ctx = {
    Math, performance, Homography: homography,
    window: {},
    state: {homographyMatrix: H, targetWidth: TW, targetHeight: TH, lockedTargetImageData: null,
            isStreaming: false, targetCorners: null, shots: []},
    cameraCanvas: {width: cw, height: ch},
    cameraCtx: {getImageData: () => ({data: camData})},
    targetCtx: {
      createImageData: (w, h) => ({width: w, height: h, data: new Uint8ClampedArray(w * h * 4)}),
      putImageData() {}
    },
    video: {readyState: 0},
    drawFallbackTarget() { throw new Error("fallback inesperado"); },
    drawCameraView() {}, drawBulletHole() {}
  };
  vm.createContext(ctx);
  vm.runInContext(input.warpSrc, ctx);
  return ctx;
}

// 1) Contagem de chamadas/alocações em projectPoint por lock
let projectCalls = 0;
const counting = {
  invertMatrix: (M) => HG.invertMatrix(M),
  projectPoint: (M, p) => { projectCalls++; return HG.projectPoint(M, p); }
};
const cctx = makeCtx(counting);
vm.runInContext("warpTargetView(true)", cctx);
const out = cctx.state.lockedTargetImageData;

// Correção do pixel retificado no centro (300,400)
const Hinv = HG.invertMatrix(H);
const ref = HG.projectPoint(Hinv, {x: 300, y: 400});
const ti = (400 * TW + 300) * 4;
const centerOk = out.data[ti] === (Math.round(ref.x) & 255) && out.data[ti + 1] === (Math.round(ref.y) & 255);

// 2) Frametime atual (mediana de 5 execuções após aquecimento)
const tctx = makeCtx(HG);
vm.runInContext("warpTargetView(true)", tctx);
const times = [];
for (let k = 0; k < 5; k++) {
  const t0 = performance.now();
  vm.runInContext("warpTargetView(true)", tctx);
  times.push(performance.now() - t0);
}
times.sort((a, b) => a - b);

// 3) Referência Zero-GC inline (h00..h22, sem objetos efêmeros) para dimensionar a Entrega 2
function warpInline(Hi, src, dstData) {
  const h00 = Hi[0][0], h01 = Hi[0][1], h02 = Hi[0][2];
  const h10 = Hi[1][0], h11 = Hi[1][1], h12 = Hi[1][2];
  const h20 = Hi[2][0], h21 = Hi[2][1], h22 = Hi[2][2];
  for (let ty = 0; ty < TH; ty++) {
    let u = h01 * ty + h02, v = h11 * ty + h12, w = h21 * ty + h22;
    let o = ty * TW * 4;
    for (let tx = 0; tx < TW; tx++, u += h00, v += h10, w += h20, o += 4) {
      const sx = Math.round(u / w), sy = Math.round(v / w);
      if (sx >= 0 && sx < cw && sy >= 0 && sy < ch) {
        const s = (sy * cw + sx) * 4;
        dstData[o] = src[s]; dstData[o + 1] = src[s + 1]; dstData[o + 2] = src[s + 2];
      } else { dstData[o] = 20; dstData[o + 1] = 22; dstData[o + 2] = 26; }
      dstData[o + 3] = 255;
    }
  }
}
const inlineDst = new Uint8ClampedArray(TW * TH * 4);
warpInline(Hinv, camData, inlineDst);
const itimes = [];
for (let k = 0; k < 5; k++) {
  const t0 = performance.now();
  warpInline(Hinv, camData, inlineDst);
  itimes.push(performance.now() - t0);
}
itimes.sort((a, b) => a - b);
let mismatches = 0;
for (let i = 0; i < inlineDst.length; i++) if (inlineDst[i] !== out.data[i]) mismatches++;

console.log(JSON.stringify({
  projectPointCalls: projectCalls,
  ephemeralObjects: projectCalls * 2,
  centerOk,
  medianMs: times[2], minMs: times[0], maxMs: times[4],
  inlineMedianMs: itimes[2],
  inlineMismatchBytes: mismatches
}));
"""

ROUNDS_SCRIPT = r"""
const fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const TA = require(input.ballistics);
const shots = [
  {id: 1, x: 300, y: 400, split: "0.00", totalTime: 0.9},
  {id: 2, x: 320, y: 380, split: "0.31", totalTime: 1.21},
  {id: 3, x: 270, y: 450, split: "0.28", totalTime: 1.49}
];
TA.syncShotsFromApp(shots);
TA.stopCurrentRound();
const st = TA.getState();
const afterStop = {history: st.roundsHistory.length, previousRound: st.previousRound !== null, roundNumber: st.roundNumber};
TA.archiveAndPrepareNextRound(false);
const st2 = TA.getState();
const afterArchive = {history: st2.roundsHistory.length, previousRound: st2.previousRound !== null,
                      roundNumber: st2.roundNumber, currentShots: st2.currentShots.length};
TA.resetAllRounds();
const st3 = TA.getState();
const afterReset = {history: st3.roundsHistory.length, previousRound: st3.previousRound !== null, roundNumber: st3.roundNumber};
console.log(JSON.stringify({afterStop, afterArchive, afterReset}));
"""


@requires_node
class TestPerformanceAndRounds(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app = read(APP_JS)
        cls.app = app
        cls.warp = run_node(WARP_SCRIPT, {"aruco": str(ARUCO_JS), "warpSrc": extract_js_function(app, "warpTargetView")})
        cls.rounds = run_node(ROUNDS_SCRIPT, {"ballistics": str(BALLISTICS_JS)})
        record("4_performance", "warpTargetView_600x800", {
            "projectPoint_chamadas": cls.warp["projectPointCalls"],
            "objetos_efemeros_estimados": cls.warp["ephemeralObjects"],
            "mediana_ms": round(cls.warp["medianMs"], 2),
            "min_ms": round(cls.warp["minMs"], 2),
            "max_ms": round(cls.warp["maxMs"], 2),
            "referencia_inline_zero_gc_ms": round(cls.warp["inlineMedianMs"], 2),
            "ganho_estimado_pct": round(100 * (1 - cls.warp["inlineMedianMs"] / cls.warp["medianMs"]), 1)
            if cls.warp["medianMs"] > 0 else None,
            "divergencia_bytes_inline_vs_atual": cls.warp["inlineMismatchBytes"],
        })
        record("4_performance", "ciclo_de_vida_rodadas", cls.rounds)

    def test_warp_produz_pixel_correto(self):
        self.assertTrue(self.warp["centerOk"])

    def test_referencia_inline_equivalente_ao_warp_atual(self):
        """A otimização planejada (Entrega 2) deve reproduzir o mesmo resultado nearest-neighbor."""
        self.assertEqual(self.warp["inlineMismatchBytes"], 0)

    def test_workers_devolvem_buffer_reciclado_via_transferable(self):
        for path in WORKER_FILES:
            src = read(path)
            self.assertIn("recycledBuffer = buffer", src, path.name)
            self.assertRegex(src, r"postMessage\(msg,\s*\[buffer\]\)", path.name)

    def test_stop_round_arquiva_snapshot(self):
        self.assertEqual(self.rounds["afterStop"]["history"], 1)

    def test_reset_all_rounds_limpa_tudo(self):
        r = self.rounds["afterReset"]
        self.assertEqual(r["history"], 0)
        self.assertFalse(r["previousRound"])
        self.assertEqual(r["roundNumber"], 1)

    @unittest.expectedFailure
    def test_defeito_1_7_warp_sem_alocacoes_por_pixel(self):
        """[Entrega 2] 480.000 chamadas a projectPoint (~960.000 objetos {x,y}) por Auto-Lock."""
        self.assertEqual(self.warp["projectPointCalls"], 0)

    @unittest.expectedFailure
    def test_defeito_1_2_buffers_reciclados_reutilizados(self):
        """[Entrega 2] recycledArucoBuffer / recycledLaserBuffer são gravados e nunca lidos."""
        reads = {}
        for name in ("recycledArucoBuffer", "recycledLaserBuffer"):
            reads[name] = len(re.findall(r"state\.%s\b(?!\s*=(?!=))" % name, self.app))
        record("4_performance", "leituras_buffers_reciclados", reads)
        self.assertTrue(all(v > 0 for v in reads.values()), reads)

    @unittest.expectedFailure
    def test_defeito_1_8_metricas_sem_loop_quadratico_duplicado(self):
        """[Entrega 2] updateBallisticMetrics() recalcula ES/MR em O(S^2) em vez de usar computeRoundSummary()."""
        src = extract_js_function(self.app, "updateBallisticMetrics")
        record("4_performance", "updateBallisticMetrics_loops_for", len(re.findall(r"\bfor\s*\(", src)))
        self.assertIn("computeRoundSummary", src)

    @unittest.expectedFailure
    def test_defeito_1_6_historico_preservado_em_nova_rodada(self):
        """[Entrega 3] archiveAndPrepareNextRound() zera roundsHistory durante a sessão ativa."""
        self.assertEqual(self.rounds["afterArchive"]["history"], 1)

    @unittest.expectedFailure
    def test_defeito_1_6_previous_round_alimenta_radar(self):
        """[Entrega 3] previousRound nunca recebe valor -> Radar Atual vs Anterior sempre vazio."""
        self.assertTrue(self.rounds["afterArchive"]["previousRound"])


# =====================================================================
# 5. Contrato Front-end <-> Back-end (/api/sessions) - diagnóstico 1.1
# =====================================================================
PAYLOAD_SCRIPT = r"""
const fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const TA = require(input.ballistics);
const shots = [
  {id: 1, x: 300, y: 400, split: "0.00", totalTime: 0.85},
  {id: 2, x: 335, y: 372, split: "0.29", totalTime: 1.14},
  {id: 3, x: 262, y: 455, split: "0.33", totalTime: 1.47},
  {id: 4, x: 590, y: 790, split: "0.41", totalTime: 1.88}
].map(s => Object.assign(s, (({zone, points}) => ({zone, points}))(TA.evaluateShotZone(s.x, s.y))));
const summary = TA.computeRoundSummary(shots, 0);
const payload = {
  session_id: "diag-frontend-001",
  target: summary.profile,
  shots: shots.map(({id, x, y, split, totalTime, zone, points}) => ({id, x, y, split, totalTime, zone, points})),
  points: summary.points,
  hitFactor: summary.hitFactor,
  time: summary.time,
  accuracyPct: summary.accuracyPct,
  extremeSpread: summary.extremeSpread,
  meanRadius: summary.meanRadius,
  zoneCounts: summary.zoneCounts,
  roundNumber: 1
};
console.log(JSON.stringify({summary, payload, bytes: Buffer.byteLength(JSON.stringify(payload))}));
"""


@requires_node
class TestFrontendBackendContract(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.data = run_node(PAYLOAD_SCRIPT, {"ballistics": str(BALLISTICS_JS)})

    def test_resumo_da_rodada_e_serializavel_e_coerente(self):
        s, p = self.data["summary"], self.data["payload"]
        self.assertEqual(s["shotCount"], len(p["shots"]))
        self.assertEqual(s["points"], sum(max(0, sh["points"]) for sh in p["shots"]) or s["points"])
        self.assertAlmostEqual(s["time"], 1.88, places=2)
        self.assertAlmostEqual(s["hitFactor"], round(s["points"] / s["time"], 2), places=2)
        self.assertLess(self.data["bytes"], servidor_dev.MAX_SESSION_PAYLOAD_BYTES)

    def test_post_e_get_api_sessions_preservam_payload_da_rodada(self):
        payload = self.data["payload"]
        cert_path, key_path = servidor_dev.gerar_certificado_autoassinado()
        ctx = servidor_dev.create_ssl_context(cert_path, key_path)
        client_ctx = ssl._create_unverified_context()
        with tempfile.TemporaryDirectory() as tmp_dir:
            httpd = servidor_dev.ThreadedHTTPSServer(
                ("127.0.0.1", 0), servidor_dev.DevRequestHandler, max_workers=4,
                db_path=Path(tmp_dir) / "sessions.db",
            )
            port = httpd.server_address[1]
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
            prev_quiet = getattr(servidor_dev.DevRequestHandler, "quiet_logs", None)
            servidor_dev.DevRequestHandler.quiet_logs = True
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                conn = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx, timeout=5)
                try:
                    raw = json.dumps(payload).encode("utf-8")
                    t0 = time.perf_counter()
                    conn.request("POST", "/api/sessions", body=raw,
                                 headers={"Content-Type": "application/json", "Content-Length": str(len(raw))})
                    r_post = conn.getresponse()
                    post_body = json.loads(r_post.read().decode("utf-8"))
                    post_ms = (time.perf_counter() - t0) * 1000
                    self.assertEqual(r_post.status, 202)
                    self.assertEqual(post_body["session_id"], payload["session_id"])

                    found = []
                    for _ in range(50):
                        conn.request("GET", f"/api/sessions?limit=5&target={payload['target']}")
                        r_get = conn.getresponse()
                        self.assertEqual(r_get.status, 200)
                        data = json.loads(r_get.read().decode("utf-8"))
                        if data["count"] >= 1:
                            found = data["sessions"]
                            break
                        time.sleep(0.02)
                finally:
                    conn.close()
            finally:
                httpd.shutdown()
                httpd.server_close()
                if prev_quiet is not None:
                    servidor_dev.DevRequestHandler.quiet_logs = prev_quiet

        self.assertEqual(len(found), 1)
        row = found[0]
        record("5_contrato_backend", "post_api_sessions", {
            "status": 202, "latencia_ms": round(post_ms, 2), "bytes": len(raw),
            "target": row["target"], "shot_count": row["shot_count"],
        })
        self.assertEqual(row["session_id"], payload["session_id"])
        self.assertEqual(row["target"], payload["target"])
        self.assertEqual(row["shot_count"], len(payload["shots"]))
        for key in ("points", "hitFactor", "time", "shots", "zoneCounts"):
            self.assertEqual(row["payload"][key], payload[key], key)

    @unittest.expectedFailure
    def test_defeito_1_1_frontend_envia_rodadas_ao_backend(self):
        """[Entrega 4] Não há nenhum fetch('/api/sessions') no front-end."""
        pattern = r"fetch\(\s*['\"`][^'\"`]*api/sessions"
        hits = {p.name: len(re.findall(pattern, read(p))) for p in (APP_JS, BALLISTICS_JS)}
        record("5_contrato_backend", "fetch_api_sessions_no_frontend", hits)
        self.assertTrue(any(hits.values()), hits)


if __name__ == "__main__":
    unittest.main(verbosity=2)
