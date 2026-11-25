/**
 * ArUco Universal Detector (4x4, 5x5, 6x6, 7x7) & 2D Homography Engine
 * Pure JavaScript - 100% Offline
 * Paridade estrita com cv2.aruco.detectMarkers: borda 100% preta, quiet-zone externa,
 * zero erros de Hamming e validacao geometrica do quarteto de marcadores (folhas A4 a A2).
 */

(function (global) {
  "use strict";

  const KNOWN_TARGETS = {
    "MANTIS 07 (IPSC)": [25, 26, 27, 24],       // TL: 25, TR: 26, BR: 27, BL: 24
    "MANTIS 01 (CONTROLE)": [1, 2, 3, 0],       // TL: 1, TR: 2, BR: 3, BL: 0
    "MANTIS 06 (IDPA)": [21, 22, 23, 20],
    "MANTIS 10 (B-27)": [9, 10, 11, 8],         // TL: 9, TR: 10, BR: 11, BL: 8
    "MANTIS 05 (HOSTAGE)": [17, 18, 19, 16],
    "ALVO VIRTU A4": [0, 1, 2, 3]               // TL: 0, TR: 1, BR: 2, BL: 3
  };

  class ArUcoUniversalDetector {
    constructor() {
      this.dictionaries = (typeof global !== "undefined" && global.ARUCO_DICTIONARIES) || {};
      this.grayBuffer = null;
      this.thresBuffer = null;
      this.integralBuffer = null;
      this.visitedBuffer = null;
      this.width = 0;
      this.height = 0;
      this._dsBuffers = {};
      this._lastGoodDim = null;
      this._gridSampleBuf = new Float64Array(100); // Buffer reutilizavel (Zero-GC) para grid ate 10x10

      // Cascata estrita OpenCV (sem 3x3 para eliminar falsos positivos)
      this.searchCascade = [
        "DICT_4X4_250",
        "DICT_5X5_250",
        "DICT_6X6_250",
        "DICT_7X7_250"
      ];

      this.activeDictionaryName = "DICT_4X4_250";
      this._lookupTables = this._buildLookupTables();
    }

    _buildLookupTables() {
      const tables = {};
      for (const [dictName, dict] of Object.entries(this.dictionaries)) {
        if (!dict || !Array.isArray(dict.markers)) continue;
        const map = new Map();
        for (let id = 0; id < dict.markers.length; id++) {
          map.set(dict.markers[id], id);
        }
        tables[dictName] = map;
      }
      return tables;
    }

    _allocateBuffers(w, h) {
      if (this.width !== w || this.height !== h) {
        this.width = w;
        this.height = h;
        this.grayBuffer = new Uint8Array(w * h);
        this.thresBuffer = new Uint8Array(w * h);
        this.integralBuffer = new Float64Array((w + 1) * (h + 1));
        this.visitedBuffer = new Uint8Array(w * h);
      }
    }

    // 1. Escala de cinza rapida + normalizacao suave apenas em cenas realmente subexpostas
    toGrayscale(rgbaData, w, h) {
      this._allocateBuffers(w, h);
      const gray = this.grayBuffer;
      const total = w * h;
      let minV = 255, maxV = 0;
      for (let i = 0, j = 0; i < total; i++, j += 4) {
        const v = (rgbaData[j] * 77 + rgbaData[j + 1] * 150 + rgbaData[j + 2] * 29) >> 8;
        gray[i] = v;
        if (v < minV) minV = v;
        if (v > maxV) maxV = v;
      }
      const range = maxV - minV;
      if (range > 18 && range < 150) {
        const k = 255 / range;
        for (let i = 0; i < total; i++) {
          gray[i] = (gray[i] - minV) * k;
        }
      }
      this._integralReady = false;
      return gray;
    }

    _buildIntegral(w, h) {
      if (this._integralReady) return;
      const gray = this.grayBuffer;
      const integral = this.integralBuffer;
      const w1 = w + 1;
      for (let x = 0; x <= w; x++) integral[x] = 0;
      for (let y = 0; y < h; y++) {
        let sum = 0;
        const rowOffset = y * w;
        const intRowOffset = (y + 1) * w1;
        const prevIntRowOffset = y * w1;
        integral[intRowOffset] = 0;
        for (let x = 0; x < w; x++) {
          sum += gray[rowOffset + x];
          integral[intRowOffset + x + 1] = integral[prevIntRowOffset + x + 1] + sum;
        }
      }
      this._integralReady = true;
    }

    // 2. Limiarizacao adaptativa por Imagem Integral (equivalente ao adaptiveThresholdMean do OpenCV)
    adaptiveThreshold(w, h, windowSize = 21, cutoff = 7) {
      this._buildIntegral(w, h);
      const gray = this.grayBuffer;
      const thres = this.thresBuffer;
      const integral = this.integralBuffer;
      const w1 = w + 1;

      const halfWin = Math.max(2, Math.floor(windowSize / 2));
      for (let y = 0; y < h; y++) {
        const y1 = Math.max(0, y - halfWin);
        const y2 = Math.min(h, y + halfWin + 1);
        const rowOffset = y * w;
        const r1 = y1 * w1;
        const r2 = y2 * w1;
        for (let x = 0; x < w; x++) {
          const x1 = Math.max(0, x - halfWin);
          const x2 = Math.min(w, x + halfWin + 1);
          const count = (x2 - x1) * (y2 - y1);
          const sum = integral[r2 + x2] - integral[r1 + x2] - integral[r2 + x1] + integral[r1 + x1];
          thres[rowOffset + x] = gray[rowOffset + x] < (sum / count) - cutoff ? 0 : 255;
        }
      }
      return thres;
    }

    // 3. Extrator de Contornos e Candidatos a Quadrilateros
    findCandidateQuads(w, h, minPerimeter = 36, maxPerimeter = 0.85 * (2 * (w + h))) {
      const thres = this.thresBuffer;
      const visited = this.visitedBuffer;
      visited.fill(0);
      const candidates = [];
      const maxSteps = 4 * (w + h);
      const rowStep = Math.max(w, h) <= 960 ? 1 : 2;

      for (let y = 2; y < h - 2; y += rowStep) {
        const rowOffset = y * w;
        for (let x = 2; x < w - 2; x++) {
          const idx = rowOffset + x;
          if (thres[idx] === 0 && thres[idx - 1] === 255 && !visited[idx]) {
            const contour = this._traceBorder(thres, visited, x, y, w, h, maxSteps);
            if (contour && contour.length >= 16) {
              const peri = contour.length;
              if (peri >= minPerimeter && peri <= maxPerimeter) {
                const quad = this._approxQuad(contour);
                if (quad && quad.length === 4 && this._isConvex(quad) && this._isPlausibleQuad(quad, minPerimeter)) {
                  candidates.push(this._orderCornersClockwise(quad));
                }
              }
            }
          }
        }
      }
      return candidates;
    }

    _quadArea(q) {
      let area = 0;
      for (let i = 0; i < 4; i++) {
        const a = q[i], b = q[(i + 1) % 4];
        area += (a.x * b.y - b.x * a.y);
      }
      return Math.abs(area) * 0.5;
    }

    // Rejeita quadrilateros degenerados, triangulos/letras esguias e perspectivas impossiveis
    _isPlausibleQuad(q, minPerimeter) {
      let minSide = Infinity, maxSide = 0;
      for (let i = 0; i < 4; i++) {
        const a = q[i], b = q[(i + 1) % 4];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        if (d < minSide) minSide = d;
        if (d > maxSide) maxSide = d;
      }
      if (minSide < Math.max(8, minPerimeter / 6)) return false;
      if (maxSide / Math.max(minSide, 1e-6) > 4.2) return false;

      // Area minima de marcador valido (pelo menos ~8x8 px)
      const area = this._quadArea(q);
      if (area < 64) return false;

      // Verifica angulos internos (rejeita quinas agudas < 25 deg ou rasas > 155 deg)
      for (let i = 0; i < 4; i++) {
        const prev = q[(i + 3) % 4];
        const curr = q[i];
        const next = q[(i + 1) % 4];
        const v1x = prev.x - curr.x, v1y = prev.y - curr.y;
        const v2x = next.x - curr.x, v2y = next.y - curr.y;
        const n1 = Math.hypot(v1x, v1y);
        const n2 = Math.hypot(v2x, v2y);
        if (n1 < 1e-6 || n2 < 1e-6) return false;
        const cosAngle = (v1x * v2x + v1y * v2y) / (n1 * n2);
        if (Math.abs(cosAngle) > 0.906) return false;
      }

      return true;
    }

    _traceBorder(thres, visited, startX, startY, w, h, maxSteps = 2400) {
      const points = [];
      let cx = startX;
      let cy = startY;

      const dx = [1, 1, 0, -1, -1, -1, 0, 1];
      const dy = [0, 1, 1, 1, 0, -1, -1, -1];
      let dir = 7;

      while (maxSteps-- > 0) {
        visited[cy * w + cx] = 1;
        points.push({ x: cx, y: cy });

        let found = false;
        const startDir = (dir + 5) % 8;
        for (let i = 0; i < 8; i++) {
          const nextDir = (startDir + i) % 8;
          const nx = cx + dx[nextDir];
          const ny = cy + dy[nextDir];
          if (nx >= 0 && nx < w && ny >= 0 && ny < h) {
            if (thres[ny * w + nx] === 0) {
              cx = nx;
              cy = ny;
              dir = nextDir;
              found = true;
              break;
            }
          }
        }

        if (!found || (cx === startX && cy === startY)) break;
      }

      return points.length >= 12 ? points : null;
    }

    _polygonPerimeter(poly) {
      let peri = 0;
      for (let i = 0; i < poly.length; i++) {
        const next = poly[(i + 1) % poly.length];
        peri += Math.hypot(poly[i].x - next.x, poly[i].y - next.y);
      }
      return peri;
    }

    _approxQuad(points) {
      const peri = this._polygonPerimeter(points);
      const closeDist = Math.max(3, peri * 0.04);
      // Limite estrito (2% a 5.5% do perimetro, alinhado ao OpenCV polygonalApproxAccuracyRate)
      // Evita que circulos, letras e manchas se transformem em falsos quadrilateros
      const ratios = [0.02, 0.03, 0.04, 0.055];
      for (let r = 0; r < ratios.length; r++) {
        let approx = this._douglasPeucker(points, peri * ratios[r]);
        if (approx.length === 5) {
          const d = Math.hypot(approx[0].x - approx[4].x, approx[0].y - approx[4].y);
          if (d < closeDist) approx = approx.slice(0, 4);
        }
        if (approx.length === 4 && this._isConvex(approx)) {
          const quadPeri = this._polygonPerimeter(approx);
          if (quadPeri < peri * 0.72) continue;
          return this._refineCorners(points, approx);
        }
      }
      return null;
    }

    // Refinamento sub-pixel: ajusta cada lado por minimos quadrados nos pontos do contorno
    _refineCorners(contour, quad) {
      const lines = [];
      for (let i = 0; i < 4; i++) {
        const a = quad[i], b = quad[(i + 1) % 4];
        const len = Math.hypot(b.x - a.x, b.y - a.y);
        if (len < 4) return quad;
        const nx = -(b.y - a.y) / len, ny = (b.x - a.x) / len;
        const band = Math.max(1.5, len * 0.06);
        let sx = 0, sy = 0, sxx = 0, syy = 0, sxy = 0, n = 0;
        for (const p of contour) {
          const t = ((p.x - a.x) * (b.x - a.x) + (p.y - a.y) * (b.y - a.y)) / (len * len);
          if (t < 0.12 || t > 0.88) continue;
          const dist = Math.abs((p.x - a.x) * nx + (p.y - a.y) * ny);
          if (dist > band) continue;
          sx += p.x; sy += p.y; sxx += p.x * p.x; syy += p.y * p.y; sxy += p.x * p.y; n++;
        }
        if (n < 3) return quad;
        const mx = sx / n, my = sy / n;
        const cxx = sxx / n - mx * mx, cyy = syy / n - my * my, cxy = sxy / n - mx * my;
        const theta = 0.5 * Math.atan2(2 * cxy, cxx - cyy);
        lines.push({ px: mx, py: my, dx: Math.cos(theta), dy: Math.sin(theta) });
      }
      const refined = [];
      for (let i = 0; i < 4; i++) {
        const L1 = lines[(i + 3) % 4], L2 = lines[i];
        const det = L1.dx * L2.dy - L1.dy * L2.dx;
        if (Math.abs(det) < 1e-6) return quad;
        const t = ((L2.px - L1.px) * L2.dy - (L2.py - L1.py) * L2.dx) / det;
        const p = { x: L1.px + t * L1.dx, y: L1.py + t * L1.dy };
        if (Math.hypot(p.x - quad[i].x, p.y - quad[i].y) > 6) return quad;
        refined.push(p);
      }
      return refined;
    }

    _douglasPeucker(points, epsilon) {
      if (points.length <= 2) return points;
      let dmax = 0;
      let index = 0;
      const first = points[0];
      const last = points[points.length - 1];

      for (let i = 1; i < points.length - 1; i++) {
        const d = this._perpendicularDistance(points[i], first, last);
        if (d > dmax) {
          index = i;
          dmax = d;
        }
      }

      if (dmax > epsilon) {
        const res1 = this._douglasPeucker(points.slice(0, index + 1), epsilon);
        const res2 = this._douglasPeucker(points.slice(index), epsilon);
        return res1.slice(0, res1.length - 1).concat(res2);
      }
      return [first, last];
    }

    _perpendicularDistance(p, lineA, lineB) {
      const dx = lineB.x - lineA.x;
      const dy = lineB.y - lineA.y;
      const mag = Math.hypot(dx, dy);
      if (mag === 0) return Math.hypot(p.x - lineA.x, p.y - lineA.y);
      return Math.abs(dy * p.x - dx * p.y + lineB.x * lineA.y - lineB.y * lineA.x) / mag;
    }

    _isConvex(quad) {
      let prevSign = 0;
      for (let i = 0; i < 4; i++) {
        const a = quad[i];
        const b = quad[(i + 1) % 4];
        const c = quad[(i + 2) % 4];
        const cross = (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x);
        const sign = cross >= 0 ? 1 : -1;
        if (prevSign === 0) prevSign = sign;
        else if (prevSign !== sign) return false;
      }
      return true;
    }

    _orderCornersClockwise(pts) {
      const cx = (pts[0].x + pts[1].x + pts[2].x + pts[3].x) / 4;
      const cy = (pts[0].y + pts[1].y + pts[2].y + pts[3].y) / 4;
      return pts.slice().sort((a, b) => Math.atan2(a.y - cy, a.x - cx) - Math.atan2(b.y - cy, b.x - cx));
    }

    _sampleBilinear(gray, w, h, x, y) {
      if (x < 0 || y < 0 || x > w - 1 || y > h - 1) return 255;
      const x0 = Math.floor(x), y0 = Math.floor(y);
      const x1 = Math.min(w - 1, x0 + 1), y1 = Math.min(h - 1, y0 + 1);
      const fx = x - x0, fy = y - y0;
      const a = gray[y0 * w + x0], b = gray[y0 * w + x1];
      const c = gray[y1 * w + x0], d = gray[y1 * w + x1];
      return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy;
    }

    // Amostragem de grid para um dado gridSize (reutilizada entre dicionarios quando necessario)
    _sampleMarkerGrid(quad, gridSize) {
      if (quad._gridBitsCache && Object.prototype.hasOwnProperty.call(quad._gridBitsCache, gridSize)) {
        return quad._gridBitsCache[gridSize];
      }
      if (!quad._gridBitsCache) {
        quad._gridBitsCache = {};
      }

      const totalCells = gridSize + 2;
      const H = Homography.getTransform(
        [{ x: 0, y: 0 }, { x: totalCells, y: 0 }, { x: totalCells, y: totalCells }, { x: 0, y: totalCells }],
        quad
      );
      if (!H) {
        quad._gridBitsCache[gridSize] = null;
        return null;
      }

      const h00 = H[0][0], h01 = H[0][1], h02 = H[0][2];
      const h10 = H[1][0], h11 = H[1][1], h12 = H[1][2];
      const h20 = H[2][0], h21 = H[2][1], h22 = H[2][2];

      const gray = this.grayBuffer;
      const w = this.width;
      const h = this.height;

      const offX = [0.5, 0.34, 0.66, 0.34, 0.66];
      const offY = [0.5, 0.34, 0.34, 0.66, 0.66];

      let borderSum = 0;
      let borderMax = 0;
      let borderCount = 0;
      let innerMax = 0;
      let innerMin = 255;
      const grid = this._gridSampleBuf;

      for (let r = 0; r < totalCells; r++) {
        const rowOff = r * totalCells;
        const isBorderRow = (r === 0 || r === totalCells - 1);
        for (let c = 0; c < totalCells; c++) {
          let cellSum = 0;
          for (let k = 0; k < 5; k++) {
            const px = c + offX[k];
            const py = r + offY[k];
            const ww = h20 * px + h21 * py + h22;
            let sx = 0, sy = 0;
            if (ww > 1e-8 || ww < -1e-8) {
              sx = (h00 * px + h01 * py + h02) / ww;
              sy = (h10 * px + h11 * py + h12) / ww;
            }
            cellSum += this._sampleBilinear(gray, w, h, sx, sy);
          }
          const val = cellSum * 0.2;
          grid[rowOff + c] = val;
          if (isBorderRow || c === 0 || c === totalCells - 1) {
            borderSum += val;
            if (val > borderMax) borderMax = val;
            borderCount++;
          } else {
            if (val > innerMax) innerMax = val;
            if (val < innerMin) innerMin = val;
          }
        }
      }

      const borderAvg = borderSum / borderCount;
      const contrast = innerMax - Math.min(borderAvg, innerMin);
      if (contrast < 28) {
        quad._gridBitsCache[gridSize] = null;
        return null;
      }

      const thresholdVal = (borderAvg + innerMax) * 0.5;

      // Regra estrita OpenCV: borda 100% preta (zero celulas brancas na borda)
      for (let r = 0; r < totalCells; r++) {
        const rowOff = r * totalCells;
        const isBorderRow = (r === 0 || r === totalCells - 1);
        for (let c = 0; c < totalCells; c++) {
          if ((isBorderRow || c === 0 || c === totalCells - 1) && grid[rowOff + c] > thresholdVal) {
            quad._gridBitsCache[gridSize] = null;
            return null;
          }
        }
      }

      // Verifica quiet-zone (margem clara ao redor da borda preta do marcador)
      const mid = totalCells * 0.5;
      const nearEnd = totalCells - 1.5;
      const far = totalCells + 0.5;
      const qx = [1.5, mid, nearEnd, far, far, far, 1.5, mid, nearEnd, -0.5, -0.5, -0.5];
      const qy = [-0.5, -0.5, -0.5, 1.5, mid, nearEnd, far, far, far, 1.5, mid, nearEnd];
      let qzInBounds = 0;
      let qzLightCount = 0;
      let qzSum = 0;
      for (let k = 0; k < 12; k++) {
        const px = qx[k], py = qy[k];
        const ww = h20 * px + h21 * py + h22;
        if (ww > -1e-8 && ww < 1e-8) continue;
        const sx = (h00 * px + h01 * py + h02) / ww;
        const sy = (h10 * px + h11 * py + h12) / ww;
        if (sx >= 1 && sx <= w - 2 && sy >= 1 && sy <= h - 2) {
          const v = this._sampleBilinear(gray, w, h, sx, sy);
          qzInBounds++;
          qzSum += v;
          if (v > thresholdVal * 0.85 && v > borderAvg + 15) {
            qzLightCount++;
          }
        }
      }
      if (qzInBounds >= 4) {
        if (qzLightCount / qzInBounds < 0.65 || (qzSum / qzInBounds) <= borderAvg + 14) {
          quad._gridBitsCache[gridSize] = null;
          return null;
        }
      }

      // Valida separacao nitida entre celulas pretas e brancas internas
      let whiteSum = 0, whiteCount = 0;
      let blackSum = 0, blackCount = 0;
      const bits = new Array(gridSize);
      for (let r = 1; r <= gridSize; r++) {
        const rowOff = r * totalCells;
        const bitRow = new Uint8Array(gridSize);
        bits[r - 1] = bitRow;
        for (let c = 1; c <= gridSize; c++) {
          const v = grid[rowOff + c];
          if (v > thresholdVal) {
            bitRow[c - 1] = 1;
            whiteSum += v;
            whiteCount++;
          } else {
            bitRow[c - 1] = 0;
            blackSum += v;
            blackCount++;
          }
        }
      }

      if (whiteCount === 0 || blackCount === 0) {
        quad._gridBitsCache[gridSize] = null;
        return null;
      }
      const whiteMean = whiteSum / whiteCount;
      const blackMean = blackSum / blackCount;
      if (whiteMean - Math.max(borderAvg, blackMean) < 26) {
        quad._gridBitsCache[gridSize] = null;
        return null;
      }

      quad._gridBitsCache[gridSize] = bits;
      return bits;
    }

    // 4. Decodificador Parametrico Estrito (4x4 a 7x7, zero erros de Hamming)
    decodeMarkerForDictionary(quad, dictName) {
      const dict = this.dictionaries[dictName];
      const lookup = this._lookupTables[dictName];
      if (!dict || !lookup) return null;

      const bits = this._sampleMarkerGrid(quad, dict.gridSize);
      if (!bits) return null;

      const gridSize = dict.gridSize;
      let matched = null;
      for (let rot = 0; rot < 4; rot++) {
        const val = this._bitsToBigInt(bits, gridSize, rot);
        const id = lookup.get(val);
        if (id !== undefined) {
          matched = { id, rot };
          break;
        }
      }
      if (!matched) return null;

      const orderedCorners = new Array(4);
      for (let i = 0; i < 4; i++) orderedCorners[i] = quad[(i + matched.rot) % 4];
      const centerX = (quad[0].x + quad[1].x + quad[2].x + quad[3].x) / 4;
      const centerY = (quad[0].y + quad[1].y + quad[2].y + quad[3].y) / 4;

      let sideSum = 0;
      for (let i = 0; i < 4; i++) {
        const a = orderedCorners[i], b = orderedCorners[(i + 1) % 4];
        sideSum += Math.hypot(a.x - b.x, a.y - b.y);
      }

      return {
        id: matched.id,
        rotation: matched.rot,
        dictionary: dictName,
        gridSize: gridSize,
        bitErrors: 0,
        avgSide: sideSum / 4,
        corners: orderedCorners,
        center: { x: centerX, y: centerY }
      };
    }

    _bitsToBigInt(bits, size, rot) {
      let val = 0n;
      const shift = BigInt(size);
      for (let r = 0; r < size; r++) {
        let rowVal = 0;
        for (let c = 0; c < size; c++) {
          let b = 0;
          if (rot === 0) b = bits[r][c];
          else if (rot === 1) b = bits[c][size - 1 - r];
          else if (rot === 2) b = bits[size - 1 - r][size - 1 - c];
          else b = bits[size - 1 - c][r];
          rowVal = (rowVal << 1) | b;
        }
        val = (val << shift) | BigInt(rowVal);
      }
      return val;
    }

    // 5. Deteccao em Cascata Universal Multi-Pass (4x4 a 7x7)
    detectMarkers(rgbaData, w, h) {
      this.toGrayscale(rgbaData, w, h);

      const maxDim = Math.max(w, h);
      const odd = v => (v % 2 === 0 ? v + 1 : v);
      const passes = [
        { win: odd(Math.max(15, Math.round(maxDim / 28))), cut: 7 },
        { win: odd(Math.max(7, Math.round(maxDim / 64))), cut: 5 }
      ];
      const minPerimeter = Math.max(32, Math.round(maxDim / 34));

      const orderedDicts = [this.activeDictionaryName];
      for (const d of this.searchCascade) {
        if (d !== this.activeDictionaryName) orderedDicts.push(d);
      }

      const passCandidates = [];
      let bestPartial = [];

      for (const dictName of orderedDicts) {
        const found = new Map();
        for (let p = 0; p < passes.length; p++) {
          if (!passCandidates[p]) {
            this.adaptiveThreshold(w, h, passes[p].win, passes[p].cut);
            passCandidates[p] = this.findCandidateQuads(w, h, minPerimeter);
          }
          for (const quad of passCandidates[p]) {
            const marker = this.decodeMarkerForDictionary(quad, dictName);
            if (!marker) continue;
            if (!found.has(marker.id)) {
              found.set(marker.id, marker);
            }
          }
          if (found.size >= 4) break;
        }

        const detected = Array.from(found.values());
        if (detected.length >= 4) {
          this.activeDictionaryName = dictName;
          return detected;
        }
        if (detected.length > bestPartial.length) bestPartial = detected;
      }

      return bestPartial;
    }

    _downsample(rgbaData, origW, origH, scale) {
      const smallW = Math.max(1, Math.round(origW / scale));
      const smallH = Math.max(1, Math.round(origH / scale));
      const key = smallW + "x" + smallH;
      let sBuf = this._dsBuffers[key];
      if (!sBuf) sBuf = this._dsBuffers[key] = new Uint8Array(smallW * smallH * 4);

      const step = Math.max(1, Math.floor(scale));
      const inv = 1 / (step * step);
      for (let sy = 0; sy < smallH; sy++) {
        const y0 = Math.min(origH - step, Math.floor(sy * scale));
        const dstRow = sy * smallW * 4;
        for (let sx = 0; sx < smallW; sx++) {
          const x0 = Math.min(origW - step, Math.floor(sx * scale));
          let r = 0, g = 0, b = 0;
          for (let yy = 0; yy < step; yy++) {
            let idx = ((y0 + yy) * origW + x0) * 4;
            for (let xx = 0; xx < step; xx++, idx += 4) {
              r += rgbaData[idx]; g += rgbaData[idx + 1]; b += rgbaData[idx + 2];
            }
          }
          const d = dstRow + sx * 4;
          sBuf[d] = r * inv; sBuf[d + 1] = g * inv; sBuf[d + 2] = b * inv; sBuf[d + 3] = 255;
        }
      }
      return { buf: sBuf, w: smallW, h: smallH };
    }

    // Piramide multi-escala (suporta folhas A4 perto ate folhas A2 distantes)
    detectMarkersDownsampled(rgbaData, origW, origH, maxDim = 640) {
      const origMax = Math.max(origW, origH);
      const levels = [];
      let dim = Math.min(maxDim, origMax);
      while (true) {
        levels.push(dim);
        if (dim >= origMax || dim >= 1920) break;
        dim = Math.min(origMax, 1920, Math.round(dim * 1.55));
      }
      if (this._lastGoodDim && levels.includes(this._lastGoodDim)) {
        levels.splice(levels.indexOf(this._lastGoodDim), 1);
        levels.unshift(this._lastGoodDim);
      }

      let bestMarkers = [];
      for (const level of levels) {
        const scale = origMax / level;
        let markers;
        if (scale <= 1.001) {
          markers = this.detectMarkers(rgbaData, origW, origH);
        } else {
          const ds = this._downsample(rgbaData, origW, origH, scale);
          markers = this.detectMarkers(ds.buf, ds.w, ds.h).map(m => ({
            id: m.id,
            rotation: m.rotation,
            dictionary: m.dictionary,
            gridSize: m.gridSize,
            bitErrors: 0,
            avgSide: m.avgSide * scale,
            corners: m.corners.map(c => ({ x: c.x * scale, y: c.y * scale })),
            center: { x: m.center.x * scale, y: m.center.y * scale }
          }));
        }
        if (markers.length >= 4) {
          this._lastGoodDim = level;
          return markers;
        }
        if (markers.length > bestMarkers.length) {
          bestMarkers = markers;
        }
      }
      return bestMarkers;
    }

    // Validacao geometrica do quarteto de marcadores (mesmo dicionario, escala consistente, convexo)
    static identifyTargetCorners(markers, frameWidth = 0, frameHeight = 0) {
      if (!Array.isArray(markers) || markers.length < 4) return null;

      // Agrupa por dicionario (nunca mistura 4x4 com 5x5, 6x6 ou 7x7)
      const byDict = new Map();
      for (const m of markers) {
        if (!m || !m.dictionary) continue;
        if (!byDict.has(m.dictionary)) byDict.set(m.dictionary, []);
        byDict.get(m.dictionary).push(m);
      }

      for (const [dictName, group] of byDict.entries()) {
        if (group.length < 4) continue;

        const idMap = new Map();
        for (const m of group) {
          if (!idMap.has(m.id)) idMap.set(m.id, m);
        }
        if (idMap.size < 4) continue;

        // 1. Verifica alvos conhecidos Mantis / Virtu A4 (DICT_4X4)
        if (dictName.startsWith("DICT_4X4")) {
          for (const [targetName, ids] of Object.entries(KNOWN_TARGETS)) {
            const [tlId, trId, brId, blId] = ids;
            if (idMap.has(tlId) && idMap.has(trId) && idMap.has(brId) && idMap.has(blId)) {
              const quartet = [idMap.get(tlId), idMap.get(trId), idMap.get(brId), idMap.get(blId)];
              if (this._validateTargetQuartet(quartet, frameWidth, frameHeight)) {
                return {
                  tl: quartet[0].center,
                  tr: quartet[1].center,
                  br: quartet[2].center,
                  bl: quartet[3].center,
                  outerCorners: this._extractOuterCorners(quartet),
                  dictionary: dictName,
                  modelName: targetName
                };
              }
            }
          }
        }

        // 2. Fallback geometrico generico para qualquer alvo 4x4, 5x5, 6x6 ou 7x7 (A4 ate A2)
        const uniqueMarkers = Array.from(idMap.values());
        const ordered = this._selectValidCornerQuartet(uniqueMarkers, frameWidth, frameHeight);
        if (ordered) {
          const dictLabel = dictName.replace("DICT_", "");
          return {
            tl: ordered[0].center,
            tr: ordered[1].center,
            br: ordered[2].center,
            bl: ordered[3].center,
            outerCorners: this._extractOuterCorners(ordered),
            dictionary: dictName,
            modelName: `ALVO ${dictLabel}`
          };
        }
      }

      return null;
    }

    static _extractOuterCorners(quartet) {
      let cx = 0, cy = 0;
      for (const m of quartet) {
        cx += m.center.x;
        cy += m.center.y;
      }
      cx /= quartet.length;
      cy /= quartet.length;

      const pickOuter = (marker) => {
        if (!marker.corners || marker.corners.length < 4) return marker.center;
        let best = marker.corners[0];
        let bestDist = -1;
        for (const pt of marker.corners) {
          const dx = pt.x - cx;
          const dy = pt.y - cy;
          const d2 = dx * dx + dy * dy;
          if (d2 > bestDist) {
            bestDist = d2;
            best = pt;
          }
        }
        return best;
      };

      return {
        tl: pickOuter(quartet[0]),
        tr: pickOuter(quartet[1]),
        br: pickOuter(quartet[2]),
        bl: pickOuter(quartet[3])
      };
    }

    static _markerSide(m) {
      if (typeof m.avgSide === "number" && m.avgSide > 0) return m.avgSide;
      if (!m.corners || m.corners.length < 4) return 0;
      let sum = 0;
      for (let i = 0; i < 4; i++) {
        const a = m.corners[i], b = m.corners[(i + 1) % 4];
        sum += Math.hypot(a.x - b.x, a.y - b.y);
      }
      return sum / 4;
    }

    static _orderTargetQuad(fourMarkers) {
      const byY = fourMarkers.slice().sort((a, b) => a.center.y - b.center.y);
      const topTwo = byY.slice(0, 2).sort((a, b) => a.center.x - b.center.x);
      const bottomTwo = byY.slice(-2).sort((a, b) => a.center.x - b.center.x);
      return [topTwo[0], topTwo[1], bottomTwo[1], bottomTwo[0]]; // TL, TR, BR, BL
    }

    static _selectValidCornerQuartet(uniqueMarkers, frameWidth, frameHeight) {
      if (uniqueMarkers.length === 4) {
        const ordered = this._orderTargetQuad(uniqueMarkers);
        return this._validateTargetQuartet(ordered, frameWidth, frameHeight) ? ordered : null;
      }

      // Se houver > 4 marcadores validos no mesmo dicionario, escolhe os 4 cantos extremos
      let cx = 0, cy = 0;
      for (const m of uniqueMarkers) {
        cx += m.center.x;
        cy += m.center.y;
      }
      cx /= uniqueMarkers.length;
      cy /= uniqueMarkers.length;

      let tl = null, tr = null, br = null, bl = null;
      let bestTl = Infinity, bestTr = -Infinity, bestBr = -Infinity, bestBl = Infinity;
      for (const m of uniqueMarkers) {
        const sum = m.center.x + m.center.y;
        const diff = m.center.x - m.center.y;
        if (sum < bestTl) { bestTl = sum; tl = m; }
        if (diff > bestTr) { bestTr = diff; tr = m; }
        if (sum > bestBr) { bestBr = sum; br = m; }
        if (diff < bestBl) { bestBl = diff; bl = m; }
      }
      const distinct = new Set([tl, tr, br, bl]);
      if (distinct.size !== 4) return null;
      const ordered = this._orderTargetQuad([tl, tr, br, bl]);
      return this._validateTargetQuartet(ordered, frameWidth, frameHeight) ? ordered : null;
    }

    static _validateTargetQuartet(ordered, frameWidth, frameHeight) {
      if (!ordered || ordered.length !== 4) return false;

      // 1. Consistencia de tamanho entre os 4 marcadores
      let minSide = Infinity, maxSide = 0, sideSum = 0;
      for (const m of ordered) {
        const s = this._markerSide(m);
        if (s <= 0) return false;
        if (s < minSide) minSide = s;
        if (s > maxSide) maxSide = s;
        sideSum += s;
      }
      if (maxSide / minSide > 2.6) return false;
      const avgMarkerSide = sideSum / 4;

      const pts = ordered.map(m => m.center);

      // 2. Convexidade estrita do quadrilatero formado pelos 4 centros
      let prevSign = 0;
      for (let i = 0; i < 4; i++) {
        const a = pts[i], b = pts[(i + 1) % 4], c = pts[(i + 2) % 4];
        const cross = (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x);
        if (Math.abs(cross) < 1e-3) return false;
        const sign = cross > 0 ? 1 : -1;
        if (prevSign === 0) prevSign = sign;
        else if (prevSign !== sign) return false;
      }

      // 3. Lados da folha devem ser proporcionalmente maiores que os marcadores
      let minEdge = Infinity, maxEdge = 0;
      for (let i = 0; i < 4; i++) {
        const a = pts[i], b = pts[(i + 1) % 4];
        const e = Math.hypot(a.x - b.x, a.y - b.y);
        if (e < minEdge) minEdge = e;
        if (e > maxEdge) maxEdge = e;
      }
      if (minEdge < avgMarkerSide * 1.8) return false;
      if (maxEdge / minEdge > 4.0) return false;

      // 4. Area minima da folha
      let area = 0;
      for (let i = 0; i < 4; i++) {
        const a = pts[i], b = pts[(i + 1) % 4];
        area += (a.x * b.y - b.x * a.y);
      }
      area = Math.abs(area) * 0.5;
      if (area < 2500) return false;
      if (frameWidth > 0 && frameHeight > 0 && area < frameWidth * frameHeight * 0.015) {
        return false;
      }

      return true;
    }
  }

  // ========================================================
  // Motor Matematico de Homografia 2D (Transformacao Projetiva)
  // ========================================================
  class Homography {
    static getTransform(srcPts, dstPts) {
      const A = [];
      const b = [];

      for (let i = 0; i < 4; i++) {
        const sx = srcPts[i].x;
        const sy = srcPts[i].y;
        const dx = dstPts[i].x;
        const dy = dstPts[i].y;

        A.push([sx, sy, 1, 0, 0, 0, -sx * dx, -sy * dx]);
        b.push(dx);
        A.push([0, 0, 0, sx, sy, 1, -sx * dy, -sy * dy]);
        b.push(dy);
      }

      const h = this._solveGaussian(A, b);
      if (!h) return null;

      return [
        [h[0], h[1], h[2]],
        [h[3], h[4], h[5]],
        [h[6], h[7], 1.0]
      ];
    }

    static invertMatrix(M) {
      const a = M[0][0], b = M[0][1], c = M[0][2];
      const d = M[1][0], e = M[1][1], f = M[1][2];
      const g = M[2][0], h = M[2][1], i = M[2][2];

      const A = (e * i - f * h);
      const B = -(d * i - f * g);
      const C = (d * h - e * g);
      const D = -(b * i - c * h);
      const E = (a * i - c * g);
      const F = -(a * h - b * g);
      const G = (b * f - c * e);
      const H_ = -(a * f - c * d);
      const I = (a * e - b * d);

      const det = a * A + b * B + c * C;
      if (Math.abs(det) < 1e-12) return null;

      const invDet = 1.0 / det;
      return [
        [A * invDet, D * invDet, G * invDet],
        [B * invDet, E * invDet, H_ * invDet],
        [C * invDet, F * invDet, I * invDet]
      ];
    }

    static projectPoint(H, pt) {
      const x = pt.x;
      const y = pt.y;
      const u = H[0][0] * x + H[0][1] * y + H[0][2];
      const v = H[1][0] * x + H[1][1] * y + H[1][2];
      const w = H[2][0] * x + H[2][1] * y + H[2][2];
      if (Math.abs(w) < 1e-8) return { x: 0, y: 0 };
      return { x: u / w, y: v / w };
    }

    static _solveGaussian(A, b) {
      const n = 8;
      for (let i = 0; i < n; i++) {
        let maxRow = i;
        for (let k = i + 1; k < n; k++) {
          if (Math.abs(A[k][i]) > Math.abs(A[maxRow][i])) {
            maxRow = k;
          }
        }
        const tmpA = A[i]; A[i] = A[maxRow]; A[maxRow] = tmpA;
        const tmpB = b[i]; b[i] = b[maxRow]; b[maxRow] = tmpB;

        if (Math.abs(A[i][i]) < 1e-12) return null;

        for (let k = i + 1; k < n; k++) {
          const factor = A[k][i] / A[i][i];
          b[k] -= factor * b[i];
          for (let j = i; j < n; j++) {
            A[k][j] -= factor * A[i][j];
          }
        }
      }

      const x = new Array(n);
      for (let i = n - 1; i >= 0; i--) {
        let sum = 0;
        for (let j = i + 1; j < n; j++) {
          sum += A[i][j] * x[j];
        }
        x[i] = (b[i] - sum) / A[i][i];
      }
      return x;
    }
  }

  global.ArUcoUniversalDetector = ArUcoUniversalDetector;
  global.Homography = Homography;

})(typeof globalThis !== "undefined" ? globalThis : (typeof self !== "undefined" ? self : window));
