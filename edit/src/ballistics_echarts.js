/**
 * Motor de Reconhecimento de Área do Alvo, Gestão de Rodadas (Shot Timer + Hit Factor)
 * e Visualizações Apache ECharts para o TARGETS_VIRTU (100% Offline).
 * Compatível com navegador comum, Capacitor WebView e testes unitários em Node.js.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.TargetAnalytics = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const TARGET_W = 600;
  const TARGET_H = 800;
  const MM_PER_PX_X = 210 / TARGET_W;
  const MM_PER_PX_Y = 297 / TARGET_H;
  const ROUNDS_STORAGE_KEY = 'targets_virtu_rounds_v1';

  const ZONE_COLORS = {
    X: '#16a34a',
    '10': '#4caf50',
    '9': '#2563eb',
    '8': '#0284c7',
    '7': '#f59e0b',
    '6': '#ea580c',
    '5': '#64748b',
    A: '#4caf50',
    C: '#2563eb',
    D: '#f59e0b',
    M: '#d32f2f'
  };

  // Geometria canônica (600x800) de cada modelo de alvo
  const IPSC_POLYGONS = {
    headA: [[262, 108], [338, 108], [338, 172], [262, 172]],
    headD: [[245, 95], [355, 95], [355, 185], [245, 185]],
    bodyA: [[248, 265], [352, 265], [352, 515], [248, 515]],
    bodyC: [[205, 235], [395, 235], [440, 305], [440, 585], [395, 650], [205, 650], [160, 585], [160, 305]],
    bodyD: [[170, 185], [430, 185], [495, 275], [495, 620], [430, 710], [170, 710], [105, 620], [105, 275]]
  };

  const IDPA_GEOM = {
    headA: [[260, 110], [340, 110], [340, 175], [260, 175]],
    headOuter: [[240, 95], [360, 95], [360, 195], [240, 195]],
    chestCircle: { cx: 300, cy: 365, r: 105 },
    bodyC: [[185, 235], [415, 235], [415, 610], [185, 610]],
    bodyD: [[120, 195], [480, 195], [480, 695], [120, 695]]
  };

  const CONCENTRIC_A4_RINGS = [
    { zone: 'X', r: 24, pts: 10 },
    { zone: '10', r: 42, pts: 10 },
    { zone: '9', r: 76, pts: 9 },
    { zone: '8', r: 114, pts: 8 },
    { zone: '7', r: 152, pts: 7 },
    { zone: '6', r: 190, pts: 6 },
    { zone: '5', r: 238, pts: 5 }
  ];

  const CONCENTRIC_B27_RINGS = [
    { zone: 'X', r: 26, pts: 10 },
    { zone: '10', r: 52, pts: 10 },
    { zone: '9', r: 92, pts: 9 },
    { zone: '8', r: 136, pts: 8 },
    { zone: '7', r: 185, pts: 7 },
    { zone: '6', r: 225, pts: 6 }
  ];

  // Estado interno
  const analyticsState = {
    profileOverride: 'AUTO',
    showZonesOverlay: false,
    detectedProfile: 'VIRTU_A4',
    lockedModelName: null,
    recognizedArea: {
      profile: 'VIRTU_A4',
      label: 'ALVO VIRTU A4 (CONCÊNTRICO)',
      source: 'padrao',
      bbox: { x: 105, y: 150, w: 390, h: 500 },
      center: { x: 300, y: 400 },
      areaPct: 40.6,
      widthMm: 137,
      heightMm: 186,
      confidence: 0.7
    },
    lastAreaScanMs: 0,
    roundState: 'idle', // 'idle' | 'preparing' | 'running' | 'stopped'
    roundNumber: 1,
    roundStartTimeMs: 0,
    elapsedSeconds: 0,
    prepareTimerId: null,
    chronoRafId: null,
    audioCtx: null,
    currentShots: [],
    previousRound: null,
    roundsHistory: [],
    charts: {
      targetMap: null,
      hfGauge: null,
      zonesBar: null,
      radarCompare: null,
      roundsEvolution: null
    },
    callbacks: {
      onClearShots: null,
      onShotsChanged: null
    }
  };

  function pointInPolygon(x, y, poly) {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const xi = poly[i][0], yi = poly[i][1];
      const xj = poly[j][0], yj = poly[j][1];
      const intersect = ((yi > y) !== (yj > y)) &&
        (x < ((xj - xi) * (y - yi)) / ((yj - yi) || 1e-9) + xi);
      if (intersect) inside = !inside;
    }
    return inside;
  }

  function mapArucoModelToProfile(modelName) {
    if (!modelName) return null;
    const upper = String(modelName).toUpperCase();
    if (upper.includes('IPSC')) return 'IPSC';
    if (upper.includes('IDPA')) return 'IDPA';
    if (upper.includes('B-27') || upper.includes('B27')) return 'B27';
    if (upper.includes('A4') || upper.includes('CONTROLE') || upper.includes('HOSTAGE')) return 'VIRTU_A4';
    return null;
  }

  function getActiveProfile() {
    if (analyticsState.profileOverride && analyticsState.profileOverride !== 'AUTO') {
      return analyticsState.profileOverride;
    }
    return analyticsState.detectedProfile || 'VIRTU_A4';
  }

  function getProfileLabel(profile) {
    switch (profile) {
      case 'IPSC': return 'IPSC CLÁSSICO (A/C/D)';
      case 'IDPA': return 'IDPA DEFESA (A/C/D)';
      case 'B27': return 'SILHUETA B-27 (X..6)';
      default: return 'ALVO VIRTU A4 (X..5)';
    }
  }

  function getDefaultCenter(profile) {
    if (profile === 'B27') return { x: 300, y: 350 };
    if (profile === 'IPSC') return { x: 300, y: 390 };
    if (profile === 'IDPA') return { x: 300, y: 365 };
    return { x: 300, y: 400 };
  }

  /**
   * Analisa o frame retificado (600x800) para reconhecer a área útil do alvo,
   * centro geométrico e dimensões em milímetros.
   */
  function analyzeTargetImageData(imageData, width, height, lockedModelName) {
    const w = width || TARGET_W;
    const h = height || TARGET_H;
    const arucoProfile = mapArucoModelToProfile(lockedModelName);
    const data = imageData && imageData.data ? imageData.data : null;

    if (!data || data.length < w * h * 4) {
      const profile = arucoProfile || getActiveProfile();
      const center = getDefaultCenter(profile);
      return {
        profile,
        label: getProfileLabel(profile),
        source: arucoProfile ? 'aruco' : 'padrao',
        bbox: { x: 105, y: 150, w: 390, h: 500 },
        center,
        areaPct: 40.6,
        widthMm: Math.round(390 * MM_PER_PX_X),
        heightMm: Math.round(500 * MM_PER_PX_Y),
        confidence: arucoProfile ? 0.92 : 0.7
      };
    }

    // Região interna que ignora as margens dos 4 marcadores ArUco
    const xStart = Math.round(w * 0.14);
    const xEnd = Math.round(w * 0.86);
    const yStart = Math.round(h * 0.11);
    const yEnd = Math.round(h * 0.89);
    const stride = 4;

    let minL = 255;
    let maxL = 0;
    const samples = [];

    for (let y = yStart; y < yEnd; y += stride) {
      const rowOffset = y * w * 4;
      for (let x = xStart; x < xEnd; x += stride) {
        const idx = rowOffset + x * 4;
        const lum = 0.299 * data[idx] + 0.587 * data[idx + 1] + 0.114 * data[idx + 2];
        if (lum < minL) minL = lum;
        if (lum > maxL) maxL = lum;
        samples.push(x, y, lum);
      }
    }

    const contrast = maxL - minL;
    let chosenProfile = arucoProfile || (analyticsState.profileOverride !== 'AUTO' ? analyticsState.profileOverride : null);

    if (contrast < 28) {
      const profile = chosenProfile || 'VIRTU_A4';
      return {
        profile,
        label: getProfileLabel(profile),
        source: arucoProfile ? 'aruco' : 'padrao',
        bbox: { x: 105, y: 150, w: 390, h: 500 },
        center: getDefaultCenter(profile),
        areaPct: 40.6,
        widthMm: Math.round(390 * MM_PER_PX_X),
        heightMm: Math.round(500 * MM_PER_PX_Y),
        confidence: arucoProfile ? 0.88 : 0.65
      };
    }

    const threshold = minL + contrast * 0.42;
    let minX = w, minY = h, maxX = 0, maxY = 0;
    let sumX = 0, sumY = 0, darkCount = 0;

    for (let i = 0; i < samples.length; i += 3) {
      const sx = samples[i];
      const sy = samples[i + 1];
      const lum = samples[i + 2];
      if (lum <= threshold) {
        if (sx < minX) minX = sx;
        if (sx > maxX) maxX = sx;
        if (sy < minY) minY = sy;
        if (sy > maxY) maxY = sy;
        sumX += sx;
        sumY += sy;
        darkCount++;
      }
    }

    if (darkCount < 25 || maxX <= minX + 20 || maxY <= minY + 20) {
      const profile = chosenProfile || 'VIRTU_A4';
      return {
        profile,
        label: getProfileLabel(profile),
        source: arucoProfile ? 'aruco' : 'padrao',
        bbox: { x: 105, y: 150, w: 390, h: 500 },
        center: getDefaultCenter(profile),
        areaPct: 40.6,
        widthMm: Math.round(390 * MM_PER_PX_X),
        heightMm: Math.round(500 * MM_PER_PX_Y),
        confidence: arucoProfile ? 0.85 : 0.6
      };
    }

    const bw = Math.max(40, maxX - minX);
    const bh = Math.max(40, maxY - minY);
    const cx = Math.round(sumX / darkCount);
    const cy = Math.round(sumY / darkCount);
    const aspect = bh / bw;

    if (!chosenProfile) {
      chosenProfile = aspect >= 1.32 ? 'IPSC' : 'VIRTU_A4';
    }

    const areaPct = Number(((bw * bh) / (w * h) * 100).toFixed(1));
    const confidence = Number(Math.min(0.99, 0.55 + Math.min(contrast, 180) / 400).toFixed(2));

    // Para alvo concêntrico, limita o desvio do centro ao núcleo para evitar ruído de sombra lateral
    const defCenter = getDefaultCenter(chosenProfile);
    const useOpticalCenter = Math.hypot(cx - defCenter.x, cy - defCenter.y) <= 55;
    const finalCenter = useOpticalCenter ? { x: cx, y: cy } : defCenter;

    return {
      profile: chosenProfile,
      label: getProfileLabel(chosenProfile),
      source: arucoProfile ? 'aruco+visao' : 'visao',
      bbox: { x: minX, y: minY, w: bw, h: bh },
      center: finalCenter,
      areaPct,
      widthMm: Math.round(bw * MM_PER_PX_X),
      heightMm: Math.round(bh * MM_PER_PX_Y),
      confidence
    };
  }

  function scanTargetArea(ctx, width, height, lockedModelName, force = false) {
    const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
    analyticsState.lockedModelName = lockedModelName || analyticsState.lockedModelName;
    if (!force && (now - analyticsState.lastAreaScanMs) < 450) {
      return analyticsState.recognizedArea;
    }
    analyticsState.lastAreaScanMs = now;

    if (!ctx || typeof ctx.getImageData !== 'function') {
      const profile = mapArucoModelToProfile(lockedModelName) || getActiveProfile();
      analyticsState.detectedProfile = profile;
      analyticsState.recognizedArea.profile = profile;
      analyticsState.recognizedArea.label = getProfileLabel(profile);
      return analyticsState.recognizedArea;
    }

    try {
      const imgData = ctx.getImageData(0, 0, width || TARGET_W, height || TARGET_H);
      const result = analyzeTargetImageData(imgData, width || TARGET_W, height || TARGET_H, analyticsState.lockedModelName);
      analyticsState.detectedProfile = result.profile;
      analyticsState.recognizedArea = result;
      updateRecognitionBadgeUI();
      return result;
    } catch (e) {
      return analyticsState.recognizedArea;
    }
  }

  /**
   * Classifica um disparo (x, y) na zona e pontuação oficial do alvo reconhecido.
   */
  function evaluateShotZone(x, y, profileOverride) {
    const profile = profileOverride || getActiveProfile();
    const center = (analyticsState.recognizedArea && analyticsState.recognizedArea.profile === profile)
      ? analyticsState.recognizedArea.center
      : getDefaultCenter(profile);

    const dx = x - center.x;
    const dy = y - center.y;
    const distPx = Math.hypot(dx, dy);
    const distMm = Number((distPx * ((MM_PER_PX_X + MM_PER_PX_Y) / 2)).toFixed(1));

    if (profile === 'IPSC') {
      if (pointInPolygon(x, y, IPSC_POLYGONS.bodyA) || pointInPolygon(x, y, IPSC_POLYGONS.headA)) {
        return { zone: 'A', points: 5, distPx, distMm, color: ZONE_COLORS.A, profile };
      }
      if (pointInPolygon(x, y, IPSC_POLYGONS.bodyC) || pointInPolygon(x, y, IPSC_POLYGONS.headD)) {
        return { zone: 'C', points: 4, distPx, distMm, color: ZONE_COLORS.C, profile };
      }
      if (pointInPolygon(x, y, IPSC_POLYGONS.bodyD)) {
        return { zone: 'D', points: 2, distPx, distMm, color: ZONE_COLORS.D, profile };
      }
      return { zone: 'M', points: -10, distPx, distMm, color: ZONE_COLORS.M, profile };
    }

    if (profile === 'IDPA') {
      const distChest = Math.hypot(x - IDPA_GEOM.chestCircle.cx, y - IDPA_GEOM.chestCircle.cy);
      if (distChest <= IDPA_GEOM.chestCircle.r || pointInPolygon(x, y, IDPA_GEOM.headA)) {
        return { zone: 'A', points: 5, distPx: distChest, distMm: Number((distChest * MM_PER_PX_X).toFixed(1)), color: ZONE_COLORS.A, profile };
      }
      if (pointInPolygon(x, y, IDPA_GEOM.bodyC) || pointInPolygon(x, y, IDPA_GEOM.headOuter)) {
        return { zone: 'C', points: 4, distPx, distMm, color: ZONE_COLORS.C, profile };
      }
      if (pointInPolygon(x, y, IDPA_GEOM.bodyD)) {
        return { zone: 'D', points: 2, distPx, distMm, color: ZONE_COLORS.D, profile };
      }
      return { zone: 'M', points: -10, distPx, distMm, color: ZONE_COLORS.M, profile };
    }

    const rings = profile === 'B27' ? CONCENTRIC_B27_RINGS : CONCENTRIC_A4_RINGS;
    for (let i = 0; i < rings.length; i++) {
      if (distPx <= rings[i].r) {
        return {
          zone: rings[i].zone,
          points: rings[i].pts,
          distPx,
          distMm,
          color: ZONE_COLORS[rings[i].zone] || '#4caf50',
          profile
        };
      }
    }
    return { zone: 'M', points: 0, distPx, distMm, color: ZONE_COLORS.M, profile };
  }

  /**
   * Desenha a área útil reconhecida e contornos das zonas sobre o canvas 600x800 do alvo.
   */
  function drawTargetZonesOverlay(ctx, width, height) {
    if (!ctx || !analyticsState.showZonesOverlay) return;
    const w = width || TARGET_W;
    const h = height || TARGET_H;
    const profile = getActiveProfile();
    const area = analyticsState.recognizedArea;
    const center = (area && area.profile === profile) ? area.center : getDefaultCenter(profile);

    ctx.save();

    // 1. Moldura técnica da área reconhecida (cantoneiras verdes 2px)
    if (area && area.bbox) {
      const b = area.bbox;
      const bracket = 18;
      ctx.strokeStyle = '#4caf50';
      ctx.lineWidth = 2;

      ctx.beginPath();
      ctx.moveTo(b.x, b.y + bracket); ctx.lineTo(b.x, b.y); ctx.lineTo(b.x + bracket, b.y);
      ctx.moveTo(b.x + b.w - bracket, b.y); ctx.lineTo(b.x + b.w, b.y); ctx.lineTo(b.x + b.w, b.y + bracket);
      ctx.moveTo(b.x + b.w, b.y + b.h - bracket); ctx.lineTo(b.x + b.w, b.y + b.h); ctx.lineTo(b.x + b.w - bracket, b.y + b.h);
      ctx.moveTo(b.x + bracket, b.y + b.h); ctx.lineTo(b.x, b.y + b.h); ctx.lineTo(b.x, b.y + b.h - bracket);
      ctx.stroke();
    }

    // 2. Zonas do perfil ativo
    if (profile === 'IPSC') {
      drawPolygonPath(ctx, IPSC_POLYGONS.bodyD, '#f59e0b', 2, [5, 4]);
      drawPolygonPath(ctx, IPSC_POLYGONS.headD, '#f59e0b', 2, [5, 4]);
      drawPolygonPath(ctx, IPSC_POLYGONS.bodyC, '#2563eb', 2, [4, 4]);
      drawPolygonPath(ctx, IPSC_POLYGONS.bodyA, '#4caf50', 2.2, []);
      drawPolygonPath(ctx, IPSC_POLYGONS.headA, '#4caf50', 2, []);
      drawZoneLabel(ctx, 300, 390, 'A', '#4caf50');
      drawZoneLabel(ctx, 418, 420, 'C', '#2563eb');
      drawZoneLabel(ctx, 468, 420, 'D', '#f59e0b');
    } else if (profile === 'IDPA') {
      drawPolygonPath(ctx, IDPA_GEOM.bodyD, '#f59e0b', 2, [5, 4]);
      drawPolygonPath(ctx, IDPA_GEOM.headOuter, '#f59e0b', 2, [5, 4]);
      drawPolygonPath(ctx, IDPA_GEOM.bodyC, '#2563eb', 2, [4, 4]);
      drawPolygonPath(ctx, IDPA_GEOM.headA, '#4caf50', 2, []);
      ctx.strokeStyle = '#4caf50';
      ctx.lineWidth = 2.2;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.arc(IDPA_GEOM.chestCircle.cx, IDPA_GEOM.chestCircle.cy, IDPA_GEOM.chestCircle.r, 0, Math.PI * 2);
      ctx.stroke();
      drawZoneLabel(ctx, IDPA_GEOM.chestCircle.cx, IDPA_GEOM.chestCircle.cy, '-0 (A)', '#4caf50');
    } else {
      const rings = profile === 'B27' ? CONCENTRIC_B27_RINGS : CONCENTRIC_A4_RINGS;
      rings.forEach((ring, idx) => {
        ctx.strokeStyle = idx <= 1 ? '#4caf50' : (idx <= 3 ? '#2563eb' : '#f59e0b');
        ctx.lineWidth = idx === 1 ? 2 : 1.5;
        ctx.setLineDash(idx === 0 ? [] : [4, 4]);
        ctx.beginPath();
        ctx.arc(center.x, center.y, ring.r, 0, Math.PI * 2);
        ctx.stroke();

        if (ring.r >= 38) {
          ctx.setLineDash([]);
          ctx.font = 'bold 11px "Segoe UI", Roboto, Arial, sans-serif';
          ctx.fillStyle = ctx.strokeStyle;
          ctx.textAlign = 'center';
          ctx.fillText(ring.zone, center.x, center.y - ring.r + 13);
        }
      });
      // Centro X
      ctx.setLineDash([]);
      ctx.strokeStyle = '#4caf50';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(center.x - 10, center.y); ctx.lineTo(center.x + 10, center.y);
      ctx.moveTo(center.x, center.y - 10); ctx.lineTo(center.x, center.y + 10);
      ctx.stroke();
    }

    // 3. Faixa compacta no rodapé do alvo (não sobrepõe área da câmera)
    ctx.setLineDash([]);
    ctx.fillStyle = 'rgba(22, 28, 36, 0.85)';
    ctx.fillRect(12, h - 34, w - 24, 24);
    ctx.strokeStyle = '#4caf50';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(12, h - 34, w - 24, 24);

    ctx.fillStyle = '#f8fafc';
    ctx.font = 'bold 11px "Segoe UI", Roboto, Arial, sans-serif';
    ctx.textAlign = 'left';
    const areaTxt = area ? `${area.label} | ÁREA ${area.areaPct}% (${area.widthMm}x${area.heightMm}mm)` : getProfileLabel(profile);
    ctx.fillText(areaTxt, 20, h - 18);

    ctx.restore();
  }

  function drawPolygonPath(ctx, pts, color, width, dash) {
    if (!pts || pts.length < 3) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.setLineDash(dash || []);
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) {
      ctx.lineTo(pts[i][0], pts[i][1]);
    }
    ctx.closePath();
    ctx.stroke();
  }

  function drawZoneLabel(ctx, x, y, text, color) {
    ctx.setLineDash([]);
    ctx.font = 'bold 13px "Segoe UI", Roboto, Arial, sans-serif';
    ctx.fillStyle = color;
    ctx.textAlign = 'center';
    ctx.fillText(text, x, y + 4);
  }

  // ==================== GESTÃO DE RODADAS & CRONÔMETRO ====================

  function playShotTimerBeep(freq = 1850, durationMs = 220) {
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      if (!analyticsState.audioCtx) {
        analyticsState.audioCtx = new AudioCtx();
      }
      if (analyticsState.audioCtx.state === 'suspended') {
        analyticsState.audioCtx.resume();
      }
      const osc = analyticsState.audioCtx.createOscillator();
      const gain = analyticsState.audioCtx.createGain();
      const now = analyticsState.audioCtx.currentTime;
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, now);
      gain.gain.setValueAtTime(0.001, now);
      gain.gain.exponentialRampToValueAtTime(0.28, now + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.001, now + durationMs / 1000);
      osc.connect(gain);
      gain.connect(analyticsState.audioCtx.destination);
      osc.start(now);
      osc.stop(now + durationMs / 1000 + 0.02);
    } catch (e) {}
  }

  function formatChrono(seconds) {
    const val = Math.max(0, Number(seconds) || 0);
    if (val >= 60) {
      const m = Math.floor(val / 60);
      const s = (val % 60).toFixed(1).padStart(4, '0');
      return `${m}:${s}`;
    }
    return `${val.toFixed(1)}s`;
  }

  let _summarySpatialCache = null;

  function _computeSpatialSummary(shots, profile) {
    const len = shots.length;
    const firstRef = len > 0 ? shots[0] : null;
    const lastRef = len > 0 ? shots[len - 1] : null;
    const c = _summarySpatialCache;

    // 1. Cache Hit O(1): nenhuma mudanca nos disparos ou perfil
    if (
      c &&
      c.shotsRef === shots &&
      c.length === len &&
      c.profile === profile &&
      c.firstShotRef === firstRef &&
      c.lastShotRef === lastRef &&
      (!lastRef || (
        lastRef.x === c.lastX &&
        lastRef.y === c.lastY &&
        lastRef.points === c.lastPoints &&
        lastRef.zone === c.lastZone &&
        lastRef.totalTime === c.lastTotalTime
      ))
    ) {
      return c;
    }

    // 2. Atualizacao Incremental O(S) quando apenas 1 novo disparo foi adicionado via push()
    if (
      c &&
      c.shotsRef === shots &&
      c.profile === profile &&
      c.length >= 1 &&
      len === c.length + 1 &&
      firstRef === c.firstShotRef &&
      shots[c.length - 1] === c.lastShotRef
    ) {
      const zone = lastRef.zone || 'M';
      const pts = typeof lastRef.points === 'number' ? lastRef.points : 0;
      const totalPoints = c.totalPoints + pts;
      const zoneCounts = Object.assign({}, c.zoneCounts);
      zoneCounts[zone] = (zoneCounts[zone] || 0) + 1;

      const sumX = c.sumX + lastRef.x;
      const sumY = c.sumY + lastRef.y;
      const cx = sumX / len;
      const cy = sumY / len;

      let extremeSpread = c.extremeSpread;
      let sumDist = Math.hypot(lastRef.x - cx, lastRef.y - cy);
      for (let i = 0; i < len - 1; i++) {
        const si = shots[i];
        const dPair = Math.hypot(si.x - lastRef.x, si.y - lastRef.y);
        if (dPair > extremeSpread) extremeSpread = dPair;
        sumDist += Math.hypot(si.x - cx, si.y - cy);
      }

      const nextCache = {
        shotsRef: shots,
        length: len,
        profile,
        firstShotRef: firstRef,
        lastShotRef: lastRef,
        lastX: lastRef.x,
        lastY: lastRef.y,
        lastPoints: lastRef.points,
        lastZone: lastRef.zone,
        lastTotalTime: lastRef.totalTime,
        totalPoints,
        zoneCounts,
        sumX,
        sumY,
        extremeSpread,
        meanRadius: sumDist / len,
        lastShotTotalTime: typeof lastRef.totalTime === 'number' ? lastRef.totalTime : null,
        lastZoneLabel: lastRef.zone || '-'
      };
      _summarySpatialCache = nextCache;
      return nextCache;
    }

    // 3. Computacao completa (cold start, reset ou troca de array/perfil)
    let totalPoints = 0;
    const zoneCounts = {};
    let sumX = 0;
    let sumY = 0;

    for (let i = 0; i < len; i++) {
      const s = shots[i];
      const zone = s.zone || 'M';
      const pts = typeof s.points === 'number' ? s.points : 0;
      totalPoints += pts;
      zoneCounts[zone] = (zoneCounts[zone] || 0) + 1;
      sumX += s.x;
      sumY += s.y;
    }

    let extremeSpread = 0;
    let meanRadius = 0;
    if (len >= 2) {
      const cx = sumX / len;
      const cy = sumY / len;
      let sumDist = 0;
      for (let i = 0; i < len; i++) {
        const si = shots[i];
        sumDist += Math.hypot(si.x - cx, si.y - cy);
        for (let j = i + 1; j < len; j++) {
          const sj = shots[j];
          const d = Math.hypot(si.x - sj.x, si.y - sj.y);
          if (d > extremeSpread) extremeSpread = d;
        }
      }
      meanRadius = sumDist / len;
    }

    const fullCache = {
      shotsRef: shots,
      length: len,
      profile,
      firstShotRef: firstRef,
      lastShotRef: lastRef,
      lastX: lastRef ? lastRef.x : null,
      lastY: lastRef ? lastRef.y : null,
      lastPoints: lastRef ? lastRef.points : null,
      lastZone: lastRef ? lastRef.zone : null,
      lastTotalTime: lastRef ? lastRef.totalTime : null,
      totalPoints,
      zoneCounts,
      sumX,
      sumY,
      extremeSpread,
      meanRadius,
      lastShotTotalTime: (lastRef && typeof lastRef.totalTime === 'number') ? lastRef.totalTime : null,
      lastZoneLabel: lastRef ? (lastRef.zone || '-') : '-'
    };
    _summarySpatialCache = fullCache;
    return fullCache;
  }

  function computeRoundSummary(shots, fallbackElapsed = 0) {
    const list = Array.isArray(shots) ? shots : [];
    const profile = getActiveProfile();
    const spatial = _computeSpatialSummary(list, profile);

    const netPoints = Math.max(0, spatial.totalPoints);
    const lastTotalTime = spatial.lastShotTotalTime !== null ? spatial.lastShotTotalTime : fallbackElapsed;
    const finalTime = Math.max(0, lastTotalTime > 0 ? lastTotalTime : fallbackElapsed);
    const hitFactor = finalTime > 0 ? netPoints / finalTime : (netPoints > 0 ? netPoints : 0);

    const maxPossible = (profile === 'IPSC' || profile === 'IDPA') ? list.length * 5 : list.length * 10;
    const accuracyPct = maxPossible > 0 ? Math.max(0, Math.min(100, (netPoints / maxPossible) * 100)) : 0;

    return {
      profile,
      shotCount: list.length,
      points: netPoints,
      rawPoints: spatial.totalPoints,
      time: Number(finalTime.toFixed(2)),
      hitFactor: Number(hitFactor.toFixed(2)),
      accuracyPct: Number(accuracyPct.toFixed(1)),
      extremeSpread: Number(spatial.extremeSpread.toFixed(1)),
      meanRadius: Number(spatial.meanRadius.toFixed(1)),
      zoneCounts: Object.assign({}, spatial.zoneCounts),
      lastZone: spatial.lastZoneLabel
    };
  }

  function updateRoundKpisUI() {
    if (typeof document === 'undefined') return;
    const summary = computeRoundSummary(analyticsState.currentShots, analyticsState.elapsedSeconds);
    const displayTime = analyticsState.roundState === 'running'
      ? analyticsState.elapsedSeconds
      : (analyticsState.currentShots.length > 0 ? summary.time : analyticsState.elapsedSeconds);

    const setEl = (id, text) => {
      const el = document.getElementById(id);
      if (el) el.innerText = text;
    };

    setEl('round-chrono', formatChrono(displayTime));
    setEl('round-hf', summary.hitFactor.toFixed(2));
    setEl('round-points', String(summary.points));
    setEl('round-badge-num', `RODADA ${analyticsState.roundNumber}`);

    const lastZoneEl = document.getElementById('round-last-zone');
    if (lastZoneEl) {
      lastZoneEl.innerText = summary.lastZone;
      lastZoneEl.style.color = ZONE_COLORS[summary.lastZone] || '';
    }

    const btnStart = document.getElementById('btn-start-round');
    if (btnStart) {
      if (analyticsState.roundState === 'preparing') {
        btnStart.innerText = 'ATENÇÃO... (CANCELAR)';
        btnStart.classList.remove('btn-primary-action');
        btnStart.classList.add('btn-danger-action');
      } else if (analyticsState.roundState === 'running') {
        btnStart.innerText = 'PARAR RODADA';
        btnStart.classList.remove('btn-primary-action');
        btnStart.classList.add('btn-danger-action');
      } else {
        btnStart.innerText = 'INICIAR RODADA';
        btnStart.classList.remove('btn-danger-action');
        btnStart.classList.add('btn-primary-action');
      }
    }
  }

  function tickChrono() {
    if (analyticsState.roundState !== 'running') return;
    const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
    analyticsState.elapsedSeconds = Math.max(0, (now - analyticsState.roundStartTimeMs) / 1000);
    updateRoundKpisUI();
    analyticsState.chronoRafId = requestAnimationFrame(tickChrono);
  }

  function startOrStopRound() {
    if (analyticsState.roundState === 'preparing') {
      if (analyticsState.prepareTimerId) {
        clearTimeout(analyticsState.prepareTimerId);
        analyticsState.prepareTimerId = null;
      }
      analyticsState.roundState = 'idle';
      updateRoundKpisUI();
      return;
    }

    if (analyticsState.roundState === 'running') {
      stopCurrentRound();
      return;
    }

    // Se a rodada anterior já teve disparos parados, avança automaticamente para nova rodada limpa
    if (analyticsState.roundState === 'stopped' && analyticsState.currentShots.length > 0) {
      archiveAndPrepareNextRound(false);
    }

    analyticsState.roundState = 'preparing';
    analyticsState.elapsedSeconds = 0;
    updateRoundKpisUI();

    const delayMs = 2000 + Math.random() * 1500;
    analyticsState.prepareTimerId = setTimeout(() => {
      analyticsState.prepareTimerId = null;
      if (analyticsState.roundState !== 'preparing') return;
      playShotTimerBeep(1850, 220);
      analyticsState.roundState = 'running';
      analyticsState.roundStartTimeMs = typeof performance !== 'undefined' ? performance.now() : Date.now();
      analyticsState.elapsedSeconds = 0;
      updateRoundKpisUI();
      if (typeof requestAnimationFrame === 'function') {
        analyticsState.chronoRafId = requestAnimationFrame(tickChrono);
      }
    }, delayMs);
  }

  function stopCurrentRound() {
    if (analyticsState.chronoRafId && typeof cancelAnimationFrame === 'function') {
      cancelAnimationFrame(analyticsState.chronoRafId);
      analyticsState.chronoRafId = null;
    }
    if (analyticsState.roundState === 'running') {
      playShotTimerBeep(1200, 140);
      const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
      analyticsState.elapsedSeconds = Math.max(0, (now - analyticsState.roundStartTimeMs) / 1000);
    }
    analyticsState.roundState = 'stopped';

    if (analyticsState.currentShots.length > 0) {
      saveRoundSnapshot();
    }
    updateRoundKpisUI();
    updateAllCharts();
    renderRoundsHistoryTable();
  }

  function saveRoundSnapshot() {
    const summary = computeRoundSummary(analyticsState.currentShots, analyticsState.elapsedSeconds);
    const snapshot = {
      roundNumber: analyticsState.roundNumber,
      profile: summary.profile,
      points: summary.points,
      time: summary.time,
      hitFactor: summary.hitFactor,
      accuracyPct: summary.accuracyPct,
      shotCount: summary.shotCount,
      extremeSpread: summary.extremeSpread,
      meanRadius: summary.meanRadius,
      zoneCounts: summary.zoneCounts,
      shots: analyticsState.currentShots.map(s => ({ ...s }))
    };

    const existingIdx = analyticsState.roundsHistory.findIndex(r => r.roundNumber === snapshot.roundNumber);
    if (existingIdx >= 0) {
      analyticsState.roundsHistory[existingIdx] = snapshot;
    } else {
      analyticsState.roundsHistory.push(snapshot);
      if (analyticsState.roundsHistory.length > 50) {
        analyticsState.roundsHistory.shift();
      }
    }
    persistRounds();
  }

  function archiveAndPrepareNextRound(clearCanvasViaCallback = true) {
    if (analyticsState.chronoRafId && typeof cancelAnimationFrame === 'function') {
      cancelAnimationFrame(analyticsState.chronoRafId);
      analyticsState.chronoRafId = null;
    }
    if (analyticsState.prepareTimerId) {
      clearTimeout(analyticsState.prepareTimerId);
      analyticsState.prepareTimerId = null;
    }

    // Modo volatil: descarta disparos e historico ao encerrar/iniciar nova rodada
    if (analyticsState.currentShots.length > 0) {
      analyticsState.roundNumber += 1;
    }

    analyticsState.roundState = 'idle';
    analyticsState.elapsedSeconds = 0;
    analyticsState.currentShots = [];
    analyticsState.previousRound = null;
    analyticsState.roundsHistory = [];

    if (clearCanvasViaCallback && typeof analyticsState.callbacks.onClearShots === 'function') {
      analyticsState.callbacks.onClearShots();
    }

    persistRounds();
    updateRoundKpisUI();
    updateAllCharts();
    renderRoundsHistoryTable();
  }

  function resetAllRounds() {
    if (analyticsState.chronoRafId && typeof cancelAnimationFrame === 'function') {
      cancelAnimationFrame(analyticsState.chronoRafId);
      analyticsState.chronoRafId = null;
    }
    if (analyticsState.prepareTimerId) {
      clearTimeout(analyticsState.prepareTimerId);
      analyticsState.prepareTimerId = null;
    }
    analyticsState.roundState = 'idle';
    analyticsState.roundNumber = 1;
    analyticsState.elapsedSeconds = 0;
    analyticsState.currentShots = [];
    analyticsState.previousRound = null;
    analyticsState.roundsHistory = [];
    try {
      if (typeof localStorage !== 'undefined') localStorage.removeItem(ROUNDS_STORAGE_KEY);
    } catch (e) {}
    if (typeof analyticsState.callbacks.onClearShots === 'function') {
      analyticsState.callbacks.onClearShots();
    }
    updateRoundKpisUI();
    updateAllCharts();
    renderRoundsHistoryTable();
  }

  /**
   * Enriquece um disparo novo com zona, pontuação e tempo acumulado de rodada.
   */
  function enrichAndRecordShot(shotObj, allCurrentShots) {
    const now = typeof performance !== 'undefined' ? performance.now() : Date.now();

    if (analyticsState.roundState === 'idle' || analyticsState.roundState === 'stopped') {
      // Inicia contagem livre automaticamente no primeiro tiro se usuário não usou Shot Timer
      if (allCurrentShots.length <= 1) {
        analyticsState.roundState = 'running';
        analyticsState.roundStartTimeMs = now;
        analyticsState.elapsedSeconds = 0;
        if (typeof requestAnimationFrame === 'function') {
          analyticsState.chronoRafId = requestAnimationFrame(tickChrono);
        }
      }
    }

    const totalTime = analyticsState.roundStartTimeMs > 0
      ? Math.max(0.01, (now - analyticsState.roundStartTimeMs) / 1000)
      : (allCurrentShots.length > 1
        ? (allCurrentShots[allCurrentShots.length - 2].totalTime || 0) + (Number(shotObj.split) || 0.25)
        : (Number(shotObj.split) || 0.01));

    const evalRes = evaluateShotZone(shotObj.x, shotObj.y);
    shotObj.zone = evalRes.zone;
    shotObj.points = evalRes.points;
    shotObj.distMm = evalRes.distMm;
    shotObj.color = evalRes.color;
    shotObj.totalTime = Number(totalTime.toFixed(2));
    shotObj.profile = evalRes.profile;

    analyticsState.currentShots = allCurrentShots;
    analyticsState.elapsedSeconds = Math.max(analyticsState.elapsedSeconds, shotObj.totalTime);

    updateRoundKpisUI();
    updateAllCharts();
    return shotObj;
  }

  function syncShotsFromApp(shots) {
    analyticsState.currentShots = (shots || []).map((s, idx) => {
      if (!s.zone || typeof s.points !== 'number') {
        const res = evaluateShotZone(s.x, s.y);
        s.zone = res.zone;
        s.points = res.points;
        s.distMm = res.distMm;
        s.color = res.color;
      }
      if (typeof s.totalTime !== 'number') {
        const prevT = idx > 0 ? (shots[idx - 1].totalTime || 0) : 0;
        s.totalTime = Number((prevT + (Number(s.split) || 0.3)).toFixed(2));
      }
      return s;
    });
    updateRoundKpisUI();
    updateAllCharts();
    renderRoundsHistoryTable();
  }

  function persistRounds() {
    // Dados 100% volateis em RAM: garante remocao de qualquer registro persistido
    try {
      if (typeof localStorage !== 'undefined') {
        localStorage.removeItem(ROUNDS_STORAGE_KEY);
      }
    } catch (e) {}
  }

  function loadPersistedRounds() {
    try {
      if (typeof localStorage !== 'undefined') {
        localStorage.removeItem(ROUNDS_STORAGE_KEY);
      }
    } catch (e) {}
  }

  // ==================== APACHE ECHARTS ENGINE ====================

  function getChartPalette() {
    const isDark = typeof document !== 'undefined' && document.body.classList.contains('dark-mode');
    return isDark ? {
      isDark: true,
      bg: '#212b36',
      surface: '#161c24',
      text: '#f1f5f9',
      muted: '#919eab',
      border: '#374151',
      splitLine: '#2d3748',
      accent: '#4caf50',
      blue: '#3b82f6',
      amber: '#f59e0b',
      red: '#ef4444'
    } : {
      isDark: false,
      bg: '#ffffff',
      surface: '#f8fafc',
      text: '#1a1a1a',
      muted: '#666666',
      border: '#cbd5e1',
      splitLine: '#e2e8f0',
      accent: '#4caf50',
      blue: '#2563eb',
      amber: '#f59e0b',
      red: '#d32f2f'
    };
  }

  function makeCirclePoints(cx, cy, r, steps = 48) {
    const pts = [];
    for (let i = 0; i <= steps; i++) {
      const a = (i / steps) * Math.PI * 2;
      pts.push([Number((cx + Math.cos(a) * r).toFixed(1)), Number((cy + Math.sin(a) * r).toFixed(1))]);
    }
    return pts;
  }

  function buildTargetMapOption(shots, palette) {
    const profile = getActiveProfile();
    const area = analyticsState.recognizedArea;
    const center = (area && area.profile === profile) ? area.center : getDefaultCenter(profile);
    const guideSeries = [];

    const addPolyLine = (name, pts, color, width, dashType) => {
      const closed = pts.slice();
      if (closed.length > 0 && (closed[0][0] !== closed[closed.length - 1][0] || closed[0][1] !== closed[closed.length - 1][1])) {
        closed.push(closed[0]);
      }
      guideSeries.push({
        name,
        type: 'line',
        data: closed,
        symbol: 'none',
        silent: true,
        animation: false,
        lineStyle: { color, width, type: dashType || 'solid' }
      });
    };

    if (profile === 'IPSC') {
      addPolyLine('IPSC D', IPSC_POLYGONS.bodyD, palette.amber, 1.8, 'dashed');
      addPolyLine('IPSC C', IPSC_POLYGONS.bodyC, palette.blue, 1.8, 'dashed');
      addPolyLine('IPSC A', IPSC_POLYGONS.bodyA, palette.accent, 2.2, 'solid');
      addPolyLine('Cabeça D', IPSC_POLYGONS.headD, palette.amber, 1.5, 'dashed');
      addPolyLine('Cabeça A', IPSC_POLYGONS.headA, palette.accent, 2, 'solid');
    } else if (profile === 'IDPA') {
      addPolyLine('IDPA -3', IDPA_GEOM.bodyD, palette.amber, 1.8, 'dashed');
      addPolyLine('IDPA -1', IDPA_GEOM.bodyC, palette.blue, 1.8, 'dashed');
      addPolyLine('IDPA -0', makeCirclePoints(IDPA_GEOM.chestCircle.cx, IDPA_GEOM.chestCircle.cy, IDPA_GEOM.chestCircle.r), palette.accent, 2.2, 'solid');
      addPolyLine('Cabeça -0', IDPA_GEOM.headA, palette.accent, 2, 'solid');
    } else {
      const rings = profile === 'B27' ? CONCENTRIC_B27_RINGS : CONCENTRIC_A4_RINGS;
      rings.forEach((ring, idx) => {
        const col = idx <= 1 ? palette.accent : (idx <= 3 ? palette.blue : palette.amber);
        addPolyLine(`Anel ${ring.zone}`, makeCirclePoints(center.x, center.y, ring.r), col, idx === 1 ? 2 : 1.4, idx === 0 ? 'solid' : 'dashed');
      });
    }

    if (area && area.bbox) {
      const b = area.bbox;
      addPolyLine('Área Reconhecida', [
        [b.x, b.y], [b.x + b.w, b.y], [b.x + b.w, b.y + b.h], [b.x, b.y + b.h]
      ], palette.accent, 1.5, 'dotted');
    }

    const shotPoints = (shots || []).map((s, idx) => ({
      name: `#${idx + 1} (${s.zone || '?'})`,
      value: [Number(s.x.toFixed(1)), Number(s.y.toFixed(1))],
      shotNum: idx + 1,
      zone: s.zone || '-',
      pts: typeof s.points === 'number' ? s.points : 0,
      split: Number(s.split) || 0,
      distMm: s.distMm || 0,
      itemStyle: {
        color: ZONE_COLORS[s.zone] || palette.red,
        borderColor: '#ffffff',
        borderWidth: 2
      }
    }));

    const mpiData = [];
    if (shots && shots.length >= 2) {
      let cx = 0, cy = 0;
      shots.forEach(s => { cx += s.x; cy += s.y; });
      mpiData.push([Number((cx / shots.length).toFixed(1)), Number((cy / shots.length).toFixed(1))]);
    }

    return {
      animation: false,
      backgroundColor: palette.surface,
      grid: { left: 10, right: 10, top: 10, bottom: 10 },
      tooltip: {
        trigger: 'item',
        backgroundColor: palette.bg,
        borderColor: palette.border,
        borderWidth: 1,
        borderRadius: 4,
        textStyle: { color: palette.text, fontFamily: '"Segoe UI", Roboto, Arial, sans-serif', fontSize: 12 },
        formatter: (params) => {
          if (params.seriesName === 'MPI') {
            return `<strong>MPI (Centro Médio)</strong><br/>X: ${params.value[0]} | Y: ${params.value[1]}`;
          }
          if (params.data && params.data.shotNum) {
            const d = params.data;
            return `<strong>Tiro #${d.shotNum}</strong> | Zona <strong>${d.zone}</strong><br/>Pontos: ${d.pts} | Split: ${d.split.toFixed(2)}s<br/>Desvio: ${d.distMm} mm`;
          }
          return '';
        }
      },
      xAxis: {
        type: 'value',
        min: 0,
        max: TARGET_W,
        axisLine: { lineStyle: { color: palette.border } },
        splitLine: { lineStyle: { color: palette.splitLine, type: 'solid' } },
        axisLabel: { show: false },
        axisTick: { show: false }
      },
      yAxis: {
        type: 'value',
        min: 0,
        max: TARGET_H,
        inverse: true,
        axisLine: { lineStyle: { color: palette.border } },
        splitLine: { lineStyle: { color: palette.splitLine, type: 'solid' } },
        axisLabel: { show: false },
        axisTick: { show: false }
      },
      series: [
        ...guideSeries,
        {
          name: 'Impactos',
          type: 'scatter',
          data: shotPoints,
          symbolSize: 14,
          label: {
            show: true,
            position: 'inside',
            formatter: (p) => String(p.data.shotNum),
            color: '#ffffff',
            fontSize: 9,
            fontWeight: 700
          }
        },
        {
          name: 'MPI',
          type: 'scatter',
          data: mpiData,
          symbol: 'diamond',
          symbolSize: 14,
          itemStyle: { color: palette.amber, borderColor: palette.text, borderWidth: 2 }
        }
      ]
    };
  }

  function buildHfGaugeOption(summary, palette) {
    const hfVal = Number(Math.min(12, Math.max(0, summary.hitFactor || 0)).toFixed(2));
    return {
      animation: true,
      animationDurationUpdate: 250,
      backgroundColor: 'transparent',
      series: [{
        type: 'gauge',
        startAngle: 180,
        endAngle: 0,
        center: ['50%', '72%'],
        radius: '96%',
        min: 0,
        max: 12,
        splitNumber: 6,
        axisLine: {
          lineStyle: {
            width: 16,
            color: [
              [0.2, palette.red],
              [0.45, palette.amber],
              [0.7, palette.blue],
              [1, palette.accent]
            ]
          }
        },
        pointer: {
          show: true,
          length: '62%',
          width: 5,
          itemStyle: { color: palette.text }
        },
        axisTick: { distance: -16, length: 5, lineStyle: { color: palette.bg, width: 1.5 } },
        splitLine: { distance: -16, length: 16, lineStyle: { color: palette.bg, width: 2 } },
        axisLabel: {
          color: palette.muted,
          distance: 20,
          fontSize: 11,
          fontWeight: 600,
          fontFamily: '"Segoe UI", Roboto, Arial, sans-serif'
        },
        detail: {
          valueAnimation: false,
          formatter: () => `${(summary.hitFactor || 0).toFixed(2)}\nHIT FACTOR`,
          color: palette.text,
          fontSize: 18,
          fontWeight: 700,
          lineHeight: 22,
          offsetCenter: [0, '24%'],
          fontFamily: '"Segoe UI", Roboto, Arial, sans-serif'
        },
        data: [{ value: hfVal }]
      }]
    };
  }

  function buildZonesBarOption(summary, palette) {
    const profile = summary.profile || getActiveProfile();
    const isTactical = profile === 'IPSC' || profile === 'IDPA';
    const categories = isTactical
      ? ['A', 'C', 'D', 'M']
      : ['X/10', '9', '8', '7', '6', '5', 'M'];

    const zc = summary.zoneCounts || {};
    const values = isTactical
      ? [zc.A || 0, zc.C || 0, zc.D || 0, zc.M || 0]
      : [(zc.X || 0) + (zc['10'] || 0), zc['9'] || 0, zc['8'] || 0, zc['7'] || 0, zc['6'] || 0, zc['5'] || 0, zc.M || 0];

    const colors = isTactical
      ? [palette.accent, palette.blue, palette.amber, palette.red]
      : [palette.accent, palette.blue, '#0284c7', palette.amber, '#ea580c', '#64748b', palette.red];

    return {
      animationDurationUpdate: 220,
      backgroundColor: 'transparent',
      grid: { left: 32, right: 14, top: 28, bottom: 28 },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'none' },
        backgroundColor: palette.bg,
        borderColor: palette.border,
        borderWidth: 1,
        borderRadius: 4,
        textStyle: { color: palette.text, fontFamily: '"Segoe UI", Roboto, Arial, sans-serif', fontSize: 12 }
      },
      xAxis: {
        type: 'category',
        data: categories,
        axisLine: { lineStyle: { color: palette.border } },
        axisTick: { show: false },
        axisLabel: { color: palette.text, fontWeight: 700, fontSize: 11 }
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        splitLine: { lineStyle: { color: palette.splitLine } },
        axisLabel: { color: palette.muted, fontSize: 11 }
      },
      series: [{
        type: 'bar',
        barMaxWidth: 32,
        data: values.map((v, i) => ({
          value: v,
          itemStyle: { color: colors[i], borderRadius: [2, 2, 0, 0] }
        })),
        label: {
          show: true,
          position: 'top',
          color: palette.text,
          fontWeight: 700,
          fontSize: 12
        }
      }]
    };
  }

  function normalizeRadarVector(summary) {
    if (!summary || summary.shotCount === 0) return [0, 0, 0, 0, 0];
    const acc = summary.accuracyPct || 0;
    const hfNorm = Math.min(100, ((summary.hitFactor || 0) / 8) * 100);
    const avgSplit = summary.time > 0 ? summary.time / summary.shotCount : 0;
    const speedNorm = avgSplit > 0 ? Math.max(0, Math.min(100, 100 - (avgSplit / 1.8) * 100)) : 50;
    const grpNorm = summary.shotCount >= 2 ? Math.max(0, Math.min(100, 100 - (summary.meanRadius / 180) * 100)) : 70;
    const ptsNorm = Math.min(100, (summary.points / Math.max(10, summary.shotCount * 8)) * 100);
    return [
      Number(acc.toFixed(1)),
      Number(hfNorm.toFixed(1)),
      Number(ptsNorm.toFixed(1)),
      Number(speedNorm.toFixed(1)),
      Number(grpNorm.toFixed(1))
    ];
  }

  function buildRadarOption(curSummary, prevSummary, palette) {
    const curVec = normalizeRadarVector(curSummary);
    const prevVec = normalizeRadarVector(prevSummary);

    return {
      animation: false,
      backgroundColor: 'transparent',
      legend: {
        data: ['Rodada Atual', 'Rodada Anterior'],
        bottom: 0,
        itemWidth: 12,
        itemHeight: 12,
        textStyle: { color: palette.text, fontSize: 11, fontWeight: 600 }
      },
      tooltip: {
        trigger: 'item',
        backgroundColor: palette.bg,
        borderColor: palette.border,
        borderWidth: 1,
        borderRadius: 4,
        textStyle: { color: palette.text, fontFamily: '"Segoe UI", Roboto, Arial, sans-serif', fontSize: 12 }
      },
      radar: {
        center: ['50%', '46%'],
        radius: '64%',
        indicator: [
          { name: 'Precisão', max: 100 },
          { name: 'Hit Factor', max: 100 },
          { name: 'Pontos', max: 100 },
          { name: 'Velocidade', max: 100 },
          { name: 'Agrupamento', max: 100 }
        ],
        axisName: { color: palette.text, fontWeight: 600, fontSize: 11 },
        splitLine: { lineStyle: { color: palette.splitLine } },
        splitArea: { areaStyle: { color: [palette.surface, palette.bg] } },
        axisLine: { lineStyle: { color: palette.border } }
      },
      series: [{
        type: 'radar',
        data: [
          {
            value: curVec,
            name: 'Rodada Atual',
            symbol: 'rect',
            symbolSize: 6,
            lineStyle: { color: palette.accent, width: 2 },
            itemStyle: { color: palette.accent },
            areaStyle: { color: palette.accent, opacity: 0.16 }
          },
          {
            value: prevVec,
            name: 'Rodada Anterior',
            symbol: 'circle',
            symbolSize: 5,
            lineStyle: { color: palette.blue, width: 2, type: 'dashed' },
            itemStyle: { color: palette.blue },
            areaStyle: { color: palette.blue, opacity: 0.1 }
          }
        ]
      }]
    };
  }

  function buildEvolutionOption(history, curSummary, palette) {
    const list = (history || []).slice();
    if (curSummary && curSummary.shotCount > 0) {
      const hasCur = list.some(r => r.roundNumber === analyticsState.roundNumber);
      if (!hasCur) {
        list.push({
          roundNumber: analyticsState.roundNumber,
          hitFactor: curSummary.hitFactor,
          time: curSummary.time,
          points: curSummary.points
        });
      }
    }

    const labels = list.map(r => `R${r.roundNumber}`);
    const hfData = list.map(r => r.hitFactor);
    const timeData = list.map(r => r.time);

    return {
      animationDurationUpdate: 220,
      backgroundColor: 'transparent',
      legend: {
        data: ['Hit Factor', 'Tempo (s)'],
        top: 0,
        textStyle: { color: palette.text, fontSize: 11, fontWeight: 600 }
      },
      tooltip: {
        trigger: 'axis',
        backgroundColor: palette.bg,
        borderColor: palette.border,
        borderWidth: 1,
        borderRadius: 4,
        textStyle: { color: palette.text, fontFamily: '"Segoe UI", Roboto, Arial, sans-serif', fontSize: 12 }
      },
      grid: { left: 38, right: 38, top: 34, bottom: 26 },
      xAxis: {
        type: 'category',
        data: labels.length > 0 ? labels : ['R1'],
        axisLine: { lineStyle: { color: palette.border } },
        axisLabel: { color: palette.text, fontWeight: 600, fontSize: 11 }
      },
      yAxis: [
        {
          type: 'value',
          name: 'HF',
          nameTextStyle: { color: palette.accent, fontSize: 10, fontWeight: 700 },
          splitLine: { lineStyle: { color: palette.splitLine } },
          axisLabel: { color: palette.muted, fontSize: 11 }
        },
        {
          type: 'value',
          name: 's',
          nameTextStyle: { color: palette.blue, fontSize: 10, fontWeight: 700 },
          splitLine: { show: false },
          axisLabel: { color: palette.muted, fontSize: 11 }
        }
      ],
      series: [
        {
          name: 'Tempo (s)',
          type: 'bar',
          yAxisIndex: 1,
          barMaxWidth: 22,
          data: timeData.length > 0 ? timeData : [0],
          itemStyle: { color: palette.blue, opacity: 0.72, borderRadius: [2, 2, 0, 0] }
        },
        {
          name: 'Hit Factor',
          type: 'line',
          smooth: false,
          symbol: 'rect',
          symbolSize: 7,
          data: hfData.length > 0 ? hfData : [0],
          lineStyle: { color: palette.accent, width: 2.5 },
          itemStyle: { color: palette.accent }
        }
      ]
    };
  }

  function ensureChartInstance(key, elementId) {
    if (typeof document === 'undefined' || typeof echarts === 'undefined') return null;
    const el = document.getElementById(elementId);
    if (!el) return null;
    if (!analyticsState.charts[key] || analyticsState.charts[key].isDisposed()) {
      analyticsState.charts[key] = echarts.init(el, null, { renderer: 'canvas' });
    }
    return analyticsState.charts[key];
  }

  function updateAllCharts() {
    if (typeof document === 'undefined' || typeof echarts === 'undefined') return;
    const statsPanel = document.getElementById('panel-stats');
    if (statsPanel && statsPanel.classList.contains('is-hidden-module')) return;
    const palette = getChartPalette();
    const curSummary = computeRoundSummary(analyticsState.currentShots, analyticsState.elapsedSeconds);
    const prevSummary = analyticsState.previousRound || null;

    const cMap = ensureChartInstance('targetMap', 'chart-target-map');
    if (cMap) cMap.setOption(buildTargetMapOption(analyticsState.currentShots, palette), true);

    const cGauge = ensureChartInstance('hfGauge', 'chart-hf-gauge');
    if (cGauge) cGauge.setOption(buildHfGaugeOption(curSummary, palette), true);

    const cZones = ensureChartInstance('zonesBar', 'chart-zones-bar');
    if (cZones) cZones.setOption(buildZonesBarOption(curSummary, palette), true);

    const cRadar = ensureChartInstance('radarCompare', 'chart-radar-compare');
    if (cRadar) cRadar.setOption(buildRadarOption(curSummary, prevSummary, palette), true);

    const cEvo = ensureChartInstance('roundsEvolution', 'chart-rounds-evolution');
    if (cEvo) cEvo.setOption(buildEvolutionOption(analyticsState.roundsHistory, curSummary, palette), true);
  }

  function resizeAllCharts() {
    Object.values(analyticsState.charts).forEach((chart) => {
      if (chart && !chart.isDisposed()) {
        try { chart.resize(); } catch (e) {}
      }
    });
  }

  function renderRoundsHistoryTable() {
    if (typeof document === 'undefined') return;
    const tbody = document.getElementById('rounds-history-body');
    if (!tbody) return;
    const rows = analyticsState.roundsHistory.slice().reverse();
    if (rows.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty-row">Nenhuma rodada encerrada</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(r => `
      <tr>
        <td><strong>R${r.roundNumber}</strong></td>
        <td>${r.profile}</td>
        <td>${r.shotCount}</td>
        <td><strong>${r.points}</strong></td>
        <td>${Number(r.time).toFixed(2)}s</td>
        <td><strong style="color:#4caf50;">${Number(r.hitFactor).toFixed(2)}</strong></td>
      </tr>
    `).join('');
  }

  function updateRecognitionBadgeUI() {
    if (typeof document === 'undefined') return;
    const badge = document.getElementById('target-recognition-info');
    const area = analyticsState.recognizedArea;
    if (badge && area) {
      badge.innerText = `${area.label} | Área ${area.areaPct}% (${area.widthMm}x${area.heightMm}mm)`;
    }
  }

  function initUI(callbacks = {}) {
    analyticsState.callbacks = callbacks || {};
    loadPersistedRounds();

    if (typeof document === 'undefined') return;

    const btnStart = document.getElementById('btn-start-round');
    if (btnStart) btnStart.addEventListener('click', startOrStopRound);

    const btnNew = document.getElementById('btn-new-round');
    if (btnNew) btnNew.addEventListener('click', () => archiveAndPrepareNextRound(true));

    const btnResetRounds = document.getElementById('btn-reset-rounds');
    if (btnResetRounds) btnResetRounds.addEventListener('click', resetAllRounds);

    const profileSel = document.getElementById('target-profile-select');
    if (profileSel) {
      profileSel.addEventListener('change', (e) => {
        analyticsState.profileOverride = e.target.value || 'AUTO';
        analyticsState.recognizedArea.profile = getActiveProfile();
        analyticsState.recognizedArea.label = getProfileLabel(analyticsState.recognizedArea.profile);
        persistRounds();
        syncShotsFromApp(analyticsState.currentShots);
        updateRecognitionBadgeUI();
        if (typeof analyticsState.callbacks.onRedrawTarget === 'function') {
          analyticsState.callbacks.onRedrawTarget();
        }
      });
    }

    const chkZones = document.getElementById('chk-show-zones');
    if (chkZones) {
      chkZones.checked = analyticsState.showZonesOverlay;
      chkZones.addEventListener('change', (e) => {
        analyticsState.showZonesOverlay = !!e.target.checked;
        if (typeof analyticsState.callbacks.onRedrawTarget === 'function') {
          analyticsState.callbacks.onRedrawTarget();
        }
      });
    }

    const btnRescan = document.getElementById('btn-rescan-area');
    if (btnRescan) {
      btnRescan.addEventListener('click', () => {
        if (typeof analyticsState.callbacks.onRescanArea === 'function') {
          analyticsState.callbacks.onRescanArea();
        }
        updateAllCharts();
      });
    }

    window.addEventListener('resize', () => {
      requestAnimationFrame(resizeAllCharts);
    });

    updateRecognitionBadgeUI();
    updateRoundKpisUI();
    renderRoundsHistoryTable();
    updateAllCharts();
  }

  return {
    TARGET_W,
    TARGET_H,
    ZONE_COLORS,
    analyzeTargetImageData,
    scanTargetArea,
    evaluateShotZone,
    drawTargetZonesOverlay,
    computeRoundSummary,
    startOrStopRound,
    stopCurrentRound,
    archiveAndPrepareNextRound,
    resetAllRounds,
    enrichAndRecordShot,
    syncShotsFromApp,
    updateAllCharts,
    resizeAllCharts,
    renderRoundsHistoryTable,
    onTabChange: () => {
      if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(() => {
          updateAllCharts();
          resizeAllCharts();
        });
      }
    },
    onThemeChange: () => {
      updateAllCharts();
    },
    initUI,
    getState: () => analyticsState
  };
}));
