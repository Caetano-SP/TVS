/**
 * TARGETS_VIRTU - Motor de Captura ao Vivo, Auto-Lock ArUco e Deteccao de Laser
 * Foco 100% em Camera Fisica ao Vivo, Rastreamento Inteligente de Alvos e Modo PiP.
 */

// ========================================================
// 1. Elementos da Interface (DOM)
// ========================================================
const video = document.getElementById("camera-stream");
const cameraCanvas = document.getElementById("camera-canvas");
const cameraCtx = cameraCanvas.getContext("2d", { willReadFrequently: true });

const targetCanvas = document.getElementById("calibrated-canvas");
const targetCtx = targetCanvas.getContext("2d");

const workspace = document.getElementById("workspace");
const tabPip = document.getElementById("tab-pip");
const tabTarget = document.getElementById("tab-target");
const tabCamera = document.getElementById("tab-camera");

const cameraSelect = document.getElementById("camera-select");
const fpsSelect = document.getElementById("fps-select");
const resolutionSelect = document.getElementById("resolution-select");
const cameraZoomSlider = document.getElementById("camera-zoom-slider");
const cameraZoomVal = document.getElementById("camera-zoom-val");
const zoomPresetButtons = document.querySelectorAll(".zoom-preset-btn");
const guideCorners = {
  tl: document.getElementById("guide-corner-tl"),
  tr: document.getElementById("guide-corner-tr"),
  br: document.getElementById("guide-corner-br"),
  bl: document.getElementById("guide-corner-bl")
};
const roiCanvas = document.createElement("canvas");
const roiCtx = roiCanvas.getContext("2d", { willReadFrequently: true });
const sliderThreshold = document.getElementById("slider-threshold");
const thresholdVal = document.getElementById("threshold-val");
const sliderPurity = document.getElementById("slider-purity");
const purityVal = document.getElementById("purity-val");
const sliderExposure = document.getElementById("slider-exposure");
const exposureVal = document.getElementById("exposure-val");

const btnAuthorizeCamera = document.getElementById("btn-authorize-camera");
const cameraPermissionGate = document.getElementById("camera-permission-gate");
const cameraLiveArea = document.getElementById("camera-live-area");
const statusDot = document.getElementById("status-dot");

const btnToggleCamera = document.getElementById("btn-toggle-camera");
const btnRecalibrate = document.getElementById("btn-recalibrate");
const btnManualCalibrate = document.getElementById("btn-manual-calibrate");
const btnFlip = document.getElementById("btn-flip");
const chkFlip = document.getElementById("chk-flip");
const darkModeToggle = document.getElementById("darkModeToggle");
const btnBeepTest = document.getElementById("btn-beep-test");
const btnClearShots = document.getElementById("btn-clear-shots");
const btnClearShotsStats = document.getElementById("btn-clear-shots-stats");
const shotsHistoryBody = document.getElementById("shots-history-body");

const badgeFps = document.getElementById("badge-fps");
const cameraFpsVal = document.getElementById("camera-fps-val");
const cameraArucoState = document.getElementById("camera-aruco-state");
const targetShotsInline = document.getElementById("target-shots-inline");
const targetLastXy = document.getElementById("target-last-xy");
const badgeTarget = document.getElementById("badge-target");
const badgeShots = document.getElementById("badge-shots");
const badgeStatus = document.getElementById("badge-status");
const cameraResLabel = document.getElementById("camera-res-label");
const targetModelLabel = document.getElementById("target-model-label");
const targetGuide = document.getElementById("target-guide");
const guideInstruction = document.getElementById("guide-instruction");
const lockProgressContainer = document.getElementById("lock-progress-container");
const lockProgressBar = document.getElementById("lock-progress-bar");
const diagnosticsPanel = document.getElementById("diagnostics-panel");

const statTotalShots = document.getElementById("stat-total-shots");
const statLastCoord = document.getElementById("stat-last-coord");
const statSplitTime = document.getElementById("stat-split-time");
const statExtremeSpread = document.getElementById("stat-extreme-spread");
const statMeanRadius = document.getElementById("stat-mean-radius");

// ========================================================
// 1a. Sistema de Navegacao por 4 Abas (Estilo MASTER_IPSC)
// ========================================================
function switchTab(tabClass) {
  const normalized = String(tabClass || "tab-camera");
  const tabKey = normalized.replace(/^tab-/, "");
  const fullClass = "tab-" + tabKey;

  document.body.classList.remove("tab-camera", "tab-alvo", "tab-estatisticas", "tab-ajustes");
  document.body.classList.add(fullClass);

  const allNavButtons = document.querySelectorAll(".nav-item[data-target], .nav-desktop-btn[data-target]");
  allNavButtons.forEach(btn => {
    const target = btn.getAttribute("data-target");
    btn.classList.toggle("active", target === tabKey || target === fullClass);
  });

  const allTabCards = document.querySelectorAll(".tab-card[data-tab]");
  allTabCards.forEach(card => {
    card.classList.toggle("active-tab", card.getAttribute("data-tab") === tabKey);
  });

  if (window.TargetAnalytics) {
    window.TargetAnalytics.onTabChange(fullClass);
  }
}
window.switchTab = switchTab;

function renderShotsTable() {
  if (!shotsHistoryBody) return;
  if (!state.shots || state.shots.length === 0) {
    shotsHistoryBody.innerHTML = '<tr><td colspan="6" class="empty-row">Nenhum disparo registrado</td></tr>';
    return;
  }

  const rowsHtml = state.shots
    .slice()
    .reverse()
    .map(s => {
      const zone = s.zone || "-";
      const pts = typeof s.points === "number" ? s.points : "--";
      const totalT = typeof s.totalTime === "number" ? `${s.totalTime.toFixed(2)}s` : "--";
      const chipColor = (window.TargetAnalytics && window.TargetAnalytics.ZONE_COLORS[zone]) || "#64748b";
      return `<tr>
        <td>#${s.id}</td>
        <td><span class="zone-chip" style="background-color:${chipColor};">${zone}</span></td>
        <td>${pts}</td>
        <td>${s.split}s</td>
        <td>${totalT}</td>
        <td>${s.x}, ${s.y}</td>
      </tr>`;
    })
    .join("");
  shotsHistoryBody.innerHTML = rowsHtml;
}

// Animation overlay canvas for hit effects
const hitAnimationCanvas = document.createElement("canvas");
hitAnimationCanvas.id = "hit-animation-canvas";
hitAnimationCanvas.style.cssText = "position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:10;";
targetCanvas.parentElement.appendChild(hitAnimationCanvas);
const hitAnimCtx = hitAnimationCanvas.getContext("2d");

// ========================================================
// 1b. Web Workers para Performance
// ========================================================
let arucoWorker = null;
let laserWorker = null;
let workersSupported = false;

function initWorkers() {
  if (typeof Worker === "undefined") {
    console.warn("Web Workers não suportados, rodando no main thread");
    return;
  }

  try {
    // Classic workers (not modules) since we use importScripts
    // Paths relative to the page origin (index.html is at /)
    arucoWorker = new Worker("workers/aruco-worker.js");
    laserWorker = new Worker("workers/laser-worker.js");

    arucoWorker.onmessage = handleArucoResult;
    arucoWorker.onerror = function(err) {
      console.warn("Erro no arucoWorker, fazendo fallback para main thread:", err);
      state.arucoWorkerBusy = false;
      workersSupported = false;
    };

    laserWorker.onmessage = handleLaserResult;
    laserWorker.onerror = function(err) {
      console.warn("Erro no laserWorker, fazendo fallback para main thread:", err);
      state.laserWorkerBusy = false;
    };

    workersSupported = true;
    console.log("Web Workers inicializados com sucesso");
  } catch (e) {
    console.warn("Falha ao inicializar workers, rodando no main thread:", e);
    workersSupported = false;
  }
}

function handleArucoResult(e) {
  state.arucoWorkerBusy = false;
  const { type, payload } = e.data || {};
  if (payload && payload.recycledBuffer instanceof ArrayBuffer) {
    state.recycledArucoBuffer = payload.recycledBuffer;
  }
  if (state.isTargetLocked || state.manualCalibrationMode) return;
  if (type === "detect-result") {
    state.detectedMarkers = payload.markers || [];
    state.markerCache = new Map(payload.markerCache || []);
    processTargetCorners(state.detectedMarkers, payload.corners || null, payload.rawCount ?? state.detectedMarkers.length);
  } else if (type === "identify-result") {
    if (payload.corners) {
      state.targetCorners = payload.corners;
      checkAutoLock();
    }
  }
}

function handleLaserResult(e) {
  state.laserWorkerBusy = false;
  const { type, payload } = e.data || {};
  if (payload && payload.recycledBuffer instanceof ArrayBuffer) {
    state.recycledLaserBuffer = payload.recycledBuffer;
  }
  if (type === "detect-result" && payload.hit) {
    registrarDisparo(payload.camX, payload.camY, performance.now());
  }
}

// ========================================================
// 2. Estado Global da Aplicacao
// ========================================================
const state = {
  isStreaming: false,
  isFlipped: false,
  currentStream: null,
  activeDeviceId: null,

  // Perfil e Métricas de Video (Padrao universal: 15 FPS + VGA 640x480)
  targetFps: parseInt(fpsSelect?.value, 10) || 15,
  resolutionKey: resolutionSelect?.value || "640x480",
  hwFrameRate: 0,
  zoomLevel: 1.0,
  hwZoom: 1.0,
  digitalZoom: 1.0,
  lastFrameTime: performance.now(),
  frameCount: 0,
  currentFps: 0,
  lastProcessedVideoTime: -1,
  lastRenderTimestamp: 0,
  videoFrameCallbackId: null,
  arucoWorkerBusy: false,
  laserWorkerBusy: false,
  recycledArucoBuffer: null,
  recycledLaserBuffer: null,

  // Motor ArUco Estrito (4x4 a 7x7) e Homografia 2D
  arucoDetector: typeof ArUcoUniversalDetector !== "undefined" ? new ArUcoUniversalDetector() : null,
  lastArucoCheckTime: 0,
  arucoIntervalMs: 100, // Busca ArUco limitada a no maximo 10 FPS para evitar aquecimento
  detectedMarkers: [],
  markerCache: new Map(), // Cache curto (200ms) apenas dentro do mesmo dicionario
  markerCacheTtlMs: 200,
  homographyMatrix: null,
  targetCorners: null, // { tl, tr, br, bl, outerCorners }

  // Auto-Lock Agil (350ms com tolerancia ao tremor do Zoom)
  isTargetLocked: false,
  autoLockStartTime: 0,
  autoLockDurationMs: 350,
  lastTargetCenter: null,

  // Calibracao Manual por Toque
  manualCalibrationMode: false,
  manualPoints: [],

  // Modo de Visualizacao: "pip", "target", "camera"
  viewMode: "pip",
  prevViewMode: "pip",
  isTransitioning: false,

  // Parametros Exclusivos de Laser Vermelho (650nm) e Filtro Anti-Lampada
  threshold: parseInt(sliderThreshold?.value, 10) || 215,
  colorPurity: parseInt(sliderPurity?.value, 10) || 42,
  exposureDimming: sliderExposure ? (parseInt(sliderExposure.value, 10) || 0) : 65,
  cooldownMs: 95, // Cooldown de 95ms (abaixo de 2 quadros @ 15 FPS) para cadências de 150ms a 220ms+
  lastShotTimestamp: 0,
  laserWarmupUntil: 0,
  laserWasActive: false,
  lastLaserTriggerX: -9999,
  lastLaserTriggerY: -9999,
  lastLaserCheckTime: 0,
  laserIntervalMs: 16,
  laserDetectionZone: null,

  // Serie de Disparos
  shots: [],

  // Dimensoes do Alvo Retificado Canônico
  targetWidth: 600,
  targetHeight: 800,

  // Animações de acerto
  hitAnimations: [],
  reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,

  // Session persistence
  sessionId: null
};

// ========================================================
// 2b. Armazenamento Volatil em RAM (Descarte ao fechar/recarregar)
// ========================================================
const STORAGE_KEY = "targets_virtu_session";

function clearVolatileStorage() {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.removeItem(STORAGE_KEY);
      localStorage.removeItem("targets_virtu_rounds_v1");
    }
    if (typeof sessionStorage !== "undefined") {
      sessionStorage.clear();
    }
  } catch (e) {}
}

function loadSession() {
  clearVolatileStorage();
  state.shots = [];
  state.sessionId = typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `sess_${Date.now()}`;
}

function saveSession() {
  // Persistencia desativada: dados mantidos apenas em memoria durante a sessao
  clearVolatileStorage();
}

window.addEventListener("beforeunload", () => {
  clearVolatileStorage();
  stopCamera();
});
window.addEventListener("pagehide", clearVolatileStorage);

// ========================================================
// 3. Sintetizador de Audio Web Audio API
// ========================================================
let audioCtx = null;

function playHitBeep(freq = 1600, durationMs = 70) {
  try {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioCtx.state === "suspended") {
      audioCtx.resume();
    }
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();

    osc.type = "sine";
    osc.frequency.setValueAtTime(freq, audioCtx.currentTime);

    gain.gain.setValueAtTime(0.3, audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + (durationMs / 1000));

    osc.connect(gain);
    gain.connect(audioCtx.destination);

    osc.start();
    osc.stop(audioCtx.currentTime + (durationMs / 1000));
  } catch (err) {
    console.error("Audio error:", err);
  }
}

function playLockSound() {
  playHitBeep(1800, 80);
  setTimeout(() => playHitBeep(2400, 120), 100);
}

// ========================================================
// 10b. Sistema de Animações de Acerto (Visual Polish)
// ========================================================
function spawnHitAnimation(x, y) {
  if (state.reducedMotion) return;

  const anim = {
    x, y,
    radius: 0,
    maxRadius: 30,
    opacity: 1,
    startTime: performance.now(),
    duration: 400
  };
  state.hitAnimations.push(anim);

  // Limit to 10 concurrent animations
  if (state.hitAnimations.length > 10) {
    state.hitAnimations.shift();
  }
}

function updateHitAnimations(now) {
  if (state.hitAnimations.length === 0) return;

  const w = targetCanvas.width;
  const h = targetCanvas.height;

  // Resize animation canvas if needed
  if (hitAnimationCanvas.width !== w || hitAnimationCanvas.height !== h) {
    hitAnimationCanvas.width = w;
    hitAnimationCanvas.height = h;
  }

  hitAnimCtx.clearRect(0, 0, w, h);

  for (let i = state.hitAnimations.length - 1; i >= 0; i--) {
    const anim = state.hitAnimations[i];
    const elapsed = now - anim.startTime;
    const progress = Math.min(1, elapsed / anim.duration);

    if (progress >= 1) {
      state.hitAnimations.splice(i, 1);
      continue;
    }

    // Easing: easeOutCubic
    const eased = 1 - Math.pow(1 - progress, 3);
    anim.radius = eased * anim.maxRadius;
    anim.opacity = 1 - eased;

    // Anel geometrico simples de confirmacao tática (Sem glow / Sem pulso conforme Regras/06)
    hitAnimCtx.beginPath();
    hitAnimCtx.arc(anim.x, anim.y, anim.radius, 0, Math.PI * 2);
    hitAnimCtx.strokeStyle = `rgba(46, 125, 50, ${anim.opacity})`;
    hitAnimCtx.lineWidth = 2;
    hitAnimCtx.stroke();
  }
}

// ========================================================
// 4. Mapeamento Estrito de Alvos (4x4 a 7x7, Folhas A4 a A2)
// ========================================================
function identifyTargetCorners(markers) {
  if (typeof ArUcoUniversalDetector === "undefined") return null;
  return ArUcoUniversalDetector.identifyTargetCorners(
    markers,
    cameraCanvas.width,
    cameraCanvas.height
  );
}

// ========================================================
// 5. Motor de Processamento de Visao Computacional
// ========================================================
function processTargetDetection(now) {
  // Se o alvo ja esta travado, nao gasta CPU procurando marcadores
  if (state.isTargetLocked || state.manualCalibrationMode) {
    return;
  }

  const w = cameraCanvas.width;
  const h = cameraCanvas.height;
  if (w === 0 || h === 0) return;

  if (workersSupported && arucoWorker) {
    if (state.arucoWorkerBusy) return;
    state.arucoWorkerBusy = true;
    try {
      const imgData = cameraCtx.getImageData(0, 0, w, h);
      const buf = imgData.data.buffer;
      arucoWorker.postMessage({
        type: "detect",
        payload: {
          buffer: buf,
          width: w,
          height: h,
          targetSize: 640,
          markerCache: Array.from(state.markerCache.entries()),
          markerCacheTtlMs: state.markerCacheTtlMs,
          now: now
        }
      }, [buf]);
    } catch (err) {
      console.warn("Fallback ArUco para main thread:", err);
      state.arucoWorkerBusy = false;
      workersSupported = false;
    }
  } else if (state.arucoDetector) {
    // Fallback: roda no main thread com mesma regra estrita
    const imgData = cameraCtx.getImageData(0, 0, w, h);
    const rawMarkers = state.arucoDetector.detectMarkersDownsampled(imgData.data, w, h, 640);
    const directCorners = identifyTargetCorners(rawMarkers);
    if (directCorners) {
      state.markerCache.clear();
      rawMarkers.forEach(m => state.markerCache.set(`${m.dictionary}:${m.id}`, { marker: m, lastSeen: now }));
      state.detectedMarkers = rawMarkers;
      processTargetCorners(rawMarkers, directCorners, rawMarkers.length);
      return;
    }

    if (rawMarkers.length >= 2) {
      const dominantDict = rawMarkers[0].dictionary;
      for (const [key, entry] of state.markerCache.entries()) {
        if (!entry || !entry.marker || entry.marker.dictionary !== dominantDict || (now - entry.lastSeen > state.markerCacheTtlMs)) {
          state.markerCache.delete(key);
        }
      }
      rawMarkers.forEach(m => {
        if (m.dictionary === dominantDict) {
          state.markerCache.set(`${m.dictionary}:${m.id}`, { marker: m, lastSeen: now });
        }
      });
    } else {
      state.markerCache.clear();
    }

    const markers = rawMarkers.length >= 2
      ? Array.from(state.markerCache.values()).map(e => e.marker)
      : rawMarkers;
    state.detectedMarkers = markers;
    processTargetCorners(markers, identifyTargetCorners(markers), rawMarkers.length);
  }
}

function updateGuideCorners(markers, hasFullQuartet = false) {
  if (!guideCorners.tl) return;
  if (hasFullQuartet) {
    Object.values(guideCorners).forEach(el => el?.classList.add("detected"));
    return;
  }
  const cw = cameraCanvas.width || 640;
  const ch = cameraCanvas.height || 480;
  const halfW = cw / 2;
  const halfH = ch / 2;
  const hit = { tl: false, tr: false, br: false, bl: false };

  if (Array.isArray(markers)) {
    for (const m of markers) {
      if (!m || !m.center) continue;
      if (m.center.x < halfW && m.center.y < halfH) hit.tl = true;
      else if (m.center.x >= halfW && m.center.y < halfH) hit.tr = true;
      else if (m.center.x >= halfW && m.center.y >= halfH) hit.br = true;
      else hit.bl = true;
    }
  }
  Object.entries(hit).forEach(([k, val]) => {
    guideCorners[k]?.classList.toggle("detected", val);
  });
}

function processTargetCorners(markers, validatedCorners = null, rawCount = null) {
  const liveCount = rawCount !== null ? rawCount : markers.length;
  const corners = validatedCorners || (markers.length >= 4 ? identifyTargetCorners(markers) : null);

  if (corners && liveCount >= 2) {
    updateGuideCorners(markers, true);
    state.targetCorners = corners;
    checkAutoLock();
    return;
  }

  // Se perdeu marcadores ou nao forma quarteto geometricamente valido
  updateGuideCorners(markers, false);
  state.targetCorners = null;
  state.autoLockStartTime = 0;
  lockProgressContainer.style.display = "none";
  state.lastTargetCenter = null;

  const statusTxt = markers.length > 0
    ? `${markers.length}/4 ARUCOS (${markers[0].dictionary.replace("DICT_", "")})`
    : "BUSCANDO 4 ARUCOS";
  if (cameraArucoState) cameraArucoState.textContent = statusTxt;
  badgeTarget.textContent = markers.length > 0
    ? `ALVO: ${markers.length}/4 MARCADORES`
    : "ALVO: BUSCANDO...";
  badgeTarget.className = "badge status-waiting";
  guideInstruction.textContent = markers.length > 0
    ? `ENCONTRADOS ${markers.length}/4 (ENQUADRE A FOLHA TODA)`
    : "APONTE PARA O ALVO (4 MARCADORES)";
  guideInstruction.style.color = "#a0aab8";
}

function checkAutoLock() {
  const corners = state.targetCorners;
  if (!corners) return;

  const now = performance.now();
  const currentCenter = {
    x: (corners.tl.x + corners.tr.x + corners.br.x + corners.bl.x) / 4,
    y: (corners.tl.y + corners.tr.y + corners.br.y + corners.bl.y) / 4
  };

  if (state.lastTargetCenter) {
    const distMovement = Math.hypot(
      currentCenter.x - state.lastTargetCenter.x,
      currentCenter.y - state.lastTargetCenter.y
    );

    if (distMovement < 28) {
      if (state.autoLockStartTime === 0) {
        state.autoLockStartTime = now;
      }

      const elapsed = now - state.autoLockStartTime;
      const progress = Math.min(100, Math.round((elapsed / state.autoLockDurationMs) * 100));

      lockProgressContainer.style.display = "block";
      lockProgressBar.style.width = `${progress}%`;
      guideInstruction.textContent = `ESTABILIZANDO... ${progress}%`;
      guideInstruction.style.color = "#00e676";

      if (elapsed >= state.autoLockDurationMs) {
        lockTargetWithCorners(corners);
        return;
      }
    } else {
      state.autoLockStartTime = now;
      lockProgressBar.style.width = "0%";
      guideInstruction.textContent = "ALINHE E MANTENHA FIRME";
      guideInstruction.style.color = "#ffb300";
    }
  } else {
    state.autoLockStartTime = now;
  }

  state.lastTargetCenter = currentCenter;
  if (cameraArucoState) {
    cameraArucoState.textContent = `4/4 ${corners.modelName}`;
  }
  badgeTarget.textContent = `ALVO: ${corners.modelName} (${state.detectedMarkers.length}/4)`;
  badgeTarget.className = "badge status-active";
}

function lockTargetWithCorners(corners) {
  state.isTargetLocked = true;
  state.targetCorners = corners;
  state.lockedTargetImageData = null;
  state.laserBgBuffer = null;
  state.laserWasActive = false;
  state.lastLaserTriggerX = -9999;
  state.lastLaserTriggerY = -9999;
  state.lastShotTimestamp = 0;
  state.laserWarmupUntil = performance.now() + 350;
  syncZoomControlsDisabled(true);
  try {
    laserWorker?.postMessage({ type: "reset-bg" });
  } catch (_) {}

  const tw = state.targetWidth;
  const th = state.targetHeight;
  const dstPts = [
    { x: 0, y: 0 },
    { x: tw - 1, y: 0 },
    { x: tw - 1, y: th - 1 },
    { x: 0, y: th - 1 }
  ];

  // Usa os cantos externos dos 4 ArUcos para manter a folha inteira no snapshot 600x800
  const mapCorners = corners.outerCorners || corners;
  const srcPts = [mapCorners.tl, mapCorners.tr, mapCorners.br, mapCorners.bl];
  const H = Homography.getTransform(srcPts, dstPts);

  if (H) {
    state.homographyMatrix = H;

    // Retifica o alvo canônico direto de frame limpo (sem contornos verdes/IDs do ArUco)
    warpTargetView(true);

    // Trava foco e balanco de branco quando suportados pelo hardware para evitar oscilacao de fundo/FPS
    applyCameraHardwareLock(true);

    // Feedback sonoro e visual imediato
    playLockSound();
    badgeTarget.textContent = `TRAVADO: ${corners.modelName}`;
    badgeTarget.className = "badge status-active";
    updateStatusBadge(true, "ALVO TRAVADO");
    targetModelLabel.textContent = `${corners.modelName} (600x800)`;
    diagnosticsPanel.textContent = `Alvo ${corners.modelName} travado com sucesso! Pronto para disparos.`;
    window.TargetAnalytics?.scanTargetArea(targetCtx, state.targetWidth, state.targetHeight, corners.modelName, true);
    window.TargetAnalytics?.updateAllCharts();

    // Muda automaticamente para a Aba 2 [Alvo Retificado]
    switchTab("tab-alvo");
  } else {
    state.isTargetLocked = false;
    syncZoomControlsDisabled(false);
  }
}

function unlockTarget() {
  state.isTargetLocked = false;
  state.targetCorners = null;
  state.homographyMatrix = null;
  state.lockedTargetImageData = null;
  state.autoLockStartTime = 0;
  state.lastTargetCenter = null;
  state.manualCalibrationMode = false;
  state.manualPoints = [];
  state.detectedMarkers = [];
  state.markerCache.clear();
  state.arucoWorkerBusy = false;
  state.laserWorkerBusy = false;
  state.laserBgBuffer = null;
  state.laserWasActive = false;
  state.lastLaserTriggerX = -9999;
  state.lastLaserTriggerY = -9999;
  updateGuideCorners([], false);
  syncZoomControlsDisabled(false);
  try {
    laserWorker?.postMessage({ type: "reset-bg" });
  } catch (_) {}
  applyCameraHardwareLock(false);

  if (lockProgressContainer) lockProgressContainer.style.display = "none";
  if (lockProgressBar) lockProgressBar.style.width = "0%";
  if (guideInstruction) {
    guideInstruction.textContent = "APONTE PARA OS 4 MARCADORES";
    guideInstruction.style.color = "#f8fafc";
  }

  switchTab("tab-camera");

  const activeTrack = state.currentStream && state.currentStream.getVideoTracks
    ? state.currentStream.getVideoTracks()[0]
    : null;

  if (!state.isStreaming || !activeTrack || activeTrack.readyState === "ended" || video.readyState < 2) {
    startCamera(cameraSelect.value || state.activeDeviceId);
  } else {
    if (video.paused) {
      video.play().catch(() => {});
    }
    updateStatusBadge(true, "CAMERA ATIVA");
    if (!state.renderLoopRunning) {
      startVideoCaptureLoop();
    }
  }

  if (cameraArucoState) cameraArucoState.textContent = "BUSCANDO 4 ARUCOS";
  badgeTarget.textContent = "ALVO: BUSCANDO...";
  badgeTarget.className = "badge status-waiting";
  targetModelLabel.textContent = "Aguardando calibração...";
  diagnosticsPanel.textContent = "Alvo destravado. Aponte a câmera para os 4 marcadores ArUco.";
  playHitBeep(800, 100);

  drawFallbackTarget();
}

// ========================================================
// 6. Retificacao de Perspectiva (Warp) e Plotagem Balistica
// ========================================================
function warpTargetView(forceCapture = false) {
  const H = state.homographyMatrix;
  if (!H) {
    drawFallbackTarget();
    return;
  }

  const tw = state.targetWidth;
  const th = state.targetHeight;
  const cw = cameraCanvas.width;
  const ch = cameraCanvas.height;

  if (forceCapture || !state.lockedTargetImageData) {
    const H_inv = Homography.invertMatrix(H);
    if (!H_inv || cw === 0 || ch === 0) {
      drawFallbackTarget();
      return;
    }

    // Garante snapshot limpo direto do <video> sem as marcacoes verdes/IDs do ArUco
    if (forceCapture && state.isStreaming && video.readyState >= 2) {
      drawCameraView(cameraCtx, cw, ch);
    }

    const camImg = cameraCtx.getImageData(0, 0, cw, ch);
    const camData = camImg.data;
    const targetImg = targetCtx.createImageData(tw, th);
    const targetData = targetImg.data;

    // Warp de alta precisão
    for (let ty = 0; ty < th; ty++) {
      const rowOffset = ty * tw * 4;
      for (let tx = 0; tx < tw; tx++) {
        const srcPt = Homography.projectPoint(H_inv, { x: tx, y: ty });
        const sx = Math.round(srcPt.x);
        const sy = Math.round(srcPt.y);

        const tidx = rowOffset + (tx * 4);
        if (sx >= 0 && sx < cw && sy >= 0 && sy < ch) {
          const sidx = (sy * cw + sx) * 4;
          targetData[tidx] = camData[sidx];
          targetData[tidx + 1] = camData[sidx + 1];
          targetData[tidx + 2] = camData[sidx + 2];
          targetData[tidx + 3] = 255;
        } else {
          targetData[tidx] = 20;
          targetData[tidx + 1] = 22;
          targetData[tidx + 2] = 26;
          targetData[tidx + 3] = 255;
        }
      }
    }
    state.lockedTargetImageData = targetImg;
  }

  targetCtx.putImageData(state.lockedTargetImageData, 0, 0);

  if (window.TargetAnalytics) {
    const modelName = state.targetCorners ? state.targetCorners.modelName : null;
    if (forceCapture) {
      window.TargetAnalytics.scanTargetArea(targetCtx, tw, th, modelName, true);
    }
    window.TargetAnalytics.drawTargetZonesOverlay(targetCtx, tw, th);
  }

  // Redesenha apenas os furos de tiro simples da serie (sem tracejos/elipses de metricas)
  const now = performance.now();
  state.shots.forEach((shot, index) => {
    const isRecent = (now - (shot.timestamp || now)) < 2000;
    drawBulletHole(targetCtx, shot.x, shot.y, index + 1, isRecent);
  });
}

function drawFallbackTarget() {
  const w = state.targetWidth;
  const h = state.targetHeight;
  const isDark = document.body.classList.contains("dark-mode");

  targetCtx.fillStyle = isDark ? "#1a232d" : "#ffffff";
  targetCtx.fillRect(0, 0, w, h);
  targetCtx.strokeStyle = isDark ? "#374151" : "#cbd5e1";
  targetCtx.lineWidth = 2;
  targetCtx.strokeRect(4, 4, w - 8, h - 8);

  if (window.TargetAnalytics) {
    const modelName = state.targetCorners ? state.targetCorners.modelName : null;
    window.TargetAnalytics.scanTargetArea(targetCtx, w, h, modelName, false);
    window.TargetAnalytics.drawTargetZonesOverlay(targetCtx, w, h);
  }

  targetCtx.fillStyle = isDark ? "#919eab" : "#666666";
  targetCtx.font = "bold 12px 'Segoe UI', Roboto, Arial, sans-serif";
  targetCtx.textAlign = "center";
  targetCtx.fillText(
    state.isStreaming
      ? "AGUARDANDO AUTO-LOCK DOS 4 MARCADORES ARUCO"
      : "ATIVE A CÂMERA NA ABA 1 PARA DETECTAR O ALVO",
    w / 2,
    30
  );
}

function drawTargetTrackingOverlay() {
  // Se estiver travado, nao polui a tela da camera com linhas verdes
  if (state.isTargetLocked) return;

  const markers = state.detectedMarkers;

  // 1. Marcadores individuais detectados
  markers.forEach(m => {
    cameraCtx.strokeStyle = "#00e676";
    cameraCtx.lineWidth = 2;
    cameraCtx.beginPath();
    cameraCtx.moveTo(m.corners[0].x, m.corners[0].y);
    cameraCtx.lineTo(m.corners[1].x, m.corners[1].y);
    cameraCtx.lineTo(m.corners[2].x, m.corners[2].y);
    cameraCtx.lineTo(m.corners[3].x, m.corners[3].y);
    cameraCtx.closePath();
    cameraCtx.stroke();

    cameraCtx.fillStyle = "#00e676";
    cameraCtx.font = "bold 11px 'Courier New', monospace";
    cameraCtx.fillText(`ID:${m.id}`, m.center.x - 12, m.center.y - 12);
  });

  // 2. Polígono dos 4 cantos
  if (state.targetCorners) {
    const c = state.targetCorners;
    cameraCtx.strokeStyle = "rgba(0, 230, 118, 0.85)";
    cameraCtx.lineWidth = 2.5;
    cameraCtx.beginPath();
    cameraCtx.moveTo(c.tl.x, c.tl.y);
    cameraCtx.lineTo(c.tr.x, c.tr.y);
    cameraCtx.lineTo(c.br.x, c.br.y);
    cameraCtx.lineTo(c.bl.x, c.bl.y);
    cameraCtx.closePath();
    cameraCtx.stroke();
  }

  // 3. Pontos de Calibracao Manual se ativo
  if (state.manualCalibrationMode) {
    state.manualPoints.forEach((p, idx) => {
      cameraCtx.fillStyle = "#ffb300";
      cameraCtx.beginPath();
      cameraCtx.arc(p.x, p.y, 6, 0, Math.PI * 2);
      cameraCtx.fill();
      cameraCtx.fillStyle = "#ffffff";
      cameraCtx.font = "bold 11px monospace";
      cameraCtx.fillText(`P${idx + 1}`, p.x + 8, p.y + 4);
    });
  }
}

function drawBulletHole(ctx, x, y, num, isRecent = false) {
  // Outer ring (bullet hole)
  ctx.beginPath();
  ctx.arc(x, y, 7, 0, Math.PI * 2);
  ctx.fillStyle = isRecent ? "#0a0e12" : "#050608";
  ctx.fill();

  // Ring color by recency: green for recent, amber for older
  ctx.lineWidth = isRecent ? 2 : 1.5;
  ctx.strokeStyle = isRecent ? "#00e676" : "#4a5a4a";
  ctx.stroke();

  // Inner dot
  ctx.beginPath();
  ctx.arc(x, y, 2, 0, Math.PI * 2);
  ctx.fillStyle = isRecent ? "#00e676" : "#3a4a3a";
  ctx.fill();

  // Shot number
  ctx.fillStyle = isRecent ? "#ffffff" : "#838e9d";
  ctx.font = "bold 9px 'Courier New', monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(num, x, y);
}

function drawGroupingOverlay(ctx, shots) {
  if (shots.length < 2) return;

  let maxDist = 0;
  let p1 = null, p2 = null;
  let sumX = 0, sumY = 0;

  for (let i = 0; i < shots.length; i++) {
    sumX += shots[i].x;
    sumY += shots[i].y;
    for (let j = i + 1; j < shots.length; j++) {
      const d = Math.hypot(shots[i].x - shots[j].x, shots[i].y - shots[j].y);
      if (d > maxDist) {
        maxDist = d;
        p1 = shots[i];
        p2 = shots[j];
      }
    }
  }

  const mpiX = sumX / shots.length;
  const mpiY = sumY / shots.length;

  // Calculate standard deviation for grouping ellipse
  let sumSqX = 0, sumSqY = 0, sumXY = 0;
  for (let i = 0; i < shots.length; i++) {
    const dx = shots[i].x - mpiX;
    const dy = shots[i].y - mpiY;
    sumSqX += dx * dx;
    sumSqY += dy * dy;
    sumXY += dx * dy;
  }
  const varX = sumSqX / shots.length;
  const varY = sumSqY / shots.length;
  const covXY = sumXY / shots.length;

  // Ellipse angle and radii (1-sigma)
  const theta = 0.5 * Math.atan2(2 * covXY, varX - varY);
  const a = Math.sqrt(Math.max(0, (varX + varY) / 2 + Math.sqrt(Math.pow((varX - varY) / 2, 2) + covXY * covXY)));
  const b = Math.sqrt(Math.max(0, (varX + varY) / 2 - Math.sqrt(Math.pow((varX - varY) / 2, 2) + covXY * covXY)));

  // Draw grouping ellipse (1-sigma)
  if (a > 1 && b > 1) {
    ctx.save();
    ctx.translate(mpiX, mpiY);
    ctx.rotate(theta);
    ctx.strokeStyle = "rgba(0, 230, 118, 0.35)";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.ellipse(0, 0, a, b, 0, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }

  // Extreme spread line
  if (p1 && p2) {
    ctx.save();
    ctx.strokeStyle = "rgba(255, 179, 0, 0.55)";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 5]);
    ctx.beginPath();
    ctx.moveTo(p1.x, p1.y);
    ctx.lineTo(p2.x, p2.y);
    ctx.stroke();
    ctx.restore();

    // ES labels at endpoints
    ctx.save();
    ctx.fillStyle = "#ffb300";
    ctx.font = "bold 8px 'Courier New', monospace";
    ctx.textAlign = "center";
    ctx.fillText("ES", (p1.x + p2.x) / 2, (p1.y + p2.y) / 2 - 8);
    ctx.restore();
  }

  // MPI crosshair
  ctx.save();
  ctx.strokeStyle = "#00e676";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(mpiX, mpiY, 5, 0, Math.PI * 2);
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(mpiX - 10, mpiY);
  ctx.lineTo(mpiX + 10, mpiY);
  ctx.moveTo(mpiX, mpiY - 10);
  ctx.lineTo(mpiX, mpiY + 10);
  ctx.stroke();

  ctx.fillStyle = "#00e676";
  ctx.font = "bold 9px 'Courier New', monospace";
  ctx.textAlign = "left";
  ctx.fillText("MPI", mpiX + 7, mpiY - 5);
  ctx.restore();
}

function updateBallisticMetrics() {
  const n = state.shots.length;
  if (n === 0) {
    statExtremeSpread.textContent = "-- px";
    statMeanRadius.textContent = "-- px";
    return;
  }
  if (n === 1) {
    statExtremeSpread.textContent = "0.0 px";
    statMeanRadius.textContent = "0.0 px";
    return;
  }

  let maxDist = 0;
  let sumX = 0, sumY = 0;
  for (let i = 0; i < n; i++) {
    sumX += state.shots[i].x;
    sumY += state.shots[i].y;
    for (let j = i + 1; j < n; j++) {
      const d = Math.hypot(state.shots[i].x - state.shots[j].x, state.shots[i].y - state.shots[j].y);
      if (d > maxDist) maxDist = d;
    }
  }

  const mpiX = sumX / n;
  const mpiY = sumY / n;
  let sumRadius = 0;
  for (let i = 0; i < n; i++) {
    sumRadius += Math.hypot(state.shots[i].x - mpiX, state.shots[i].y - mpiY);
  }
  const meanRadius = sumRadius / n;

  statExtremeSpread.textContent = `${maxDist.toFixed(1)} px`;
  statMeanRadius.textContent = `${meanRadius.toFixed(1)} px`;
}

// ========================================================
// 7. Deteccao Optica Eficiente de Laser Vermelho (Economia de Bateria + Alta Sensibilidade)
// ========================================================
function getDigitalZoomSourceRect() {
  const vw = video.videoWidth || cameraCanvas.width || 640;
  const vh = video.videoHeight || cameraCanvas.height || 480;
  const dz = Math.max(1.0, Number(state.digitalZoom) || 1.0);
  const visW = vw / dz;
  const visH = vh / dz;
  const visX = (vw - visW) / 2;
  const visY = (vh - visH) / 2;
  return { vw, vh, visX, visY, visW, visH };
}

function drawCameraView(ctx, dstW, dstH) {
  const { visX, visY, visW, visH } = getDigitalZoomSourceRect();
  ctx.save();
  if (state.isFlipped) {
    ctx.translate(dstW, 0);
    ctx.scale(-1, 1);
  }
  ctx.drawImage(video, visX, visY, visW, visH, 0, 0, dstW, dstH);
  ctx.restore();
}

function extractLaserRoiImageData(startX, startY, roiW, roiH, cw, ch) {
  // Quando travado e fora da aba Camera, recorta apenas a ROI direto do <video> (ja com Zoom/Espelho)
  const needsFullCanvas = !state.isTargetLocked || document.body.classList.contains("tab-camera");
  if (!needsFullCanvas && video.readyState >= 2) {
    if (roiCanvas.width !== roiW || roiCanvas.height !== roiH) {
      roiCanvas.width = roiW;
      roiCanvas.height = roiH;
    }
    const { visX, visY, visW, visH } = getDigitalZoomSourceRect();
    const safeCh = ch || cameraCanvas.height || 480;
    const srcY = visY + (startY / safeCh) * visH;
    const srcW = (roiW / cw) * visW;
    const srcH = (roiH / safeCh) * visH;

    if (state.isFlipped) {
      const srcX = visX + ((cw - (startX + roiW)) / cw) * visW;
      roiCtx.save();
      roiCtx.translate(roiW, 0);
      roiCtx.scale(-1, 1);
      roiCtx.drawImage(video, srcX, srcY, srcW, srcH, 0, 0, roiW, roiH);
      roiCtx.restore();
    } else {
      const srcX = visX + (startX / cw) * visW;
      roiCtx.drawImage(video, srcX, srcY, srcW, srcH, 0, 0, roiW, roiH);
    }
    return roiCtx.getImageData(0, 0, roiW, roiH);
  }
  return cameraCtx.getImageData(startX, startY, roiW, roiH);
}

function detectLaserImpulse(now) {
  state.lastLaserCheckTime = now;

  // Aguarda estabilizacao da trava de exposicao/foco apos o lock para evitar disparo fantasma inicial
  if (state.laserWarmupUntil && now < state.laserWarmupUntil) {
    return;
  }

  const w = cameraCanvas.width;
  const h = cameraCanvas.height;
  if (w === 0 || h === 0) return;

  // Delimita a regiao de interesse (ROI) pelos cantos externos da folha (+ margem de 24px)
  let startX = 0, endX = w, startY = 0, endY = h;
  if (state.targetCorners) {
    const c = state.targetCorners.outerCorners || state.targetCorners;
    const minX = Math.min(c.tl.x, c.bl.x, c.tr.x, c.br.x) - 24;
    const maxX = Math.max(c.tl.x, c.bl.x, c.tr.x, c.br.x) + 24;
    const minY = Math.min(c.tl.y, c.tr.y, c.bl.y, c.br.y) - 24;
    const maxY = Math.max(c.tl.y, c.tr.y, c.bl.y, c.br.y) + 24;
    startX = Math.max(0, Math.floor(minX));
    endX = Math.min(w, Math.ceil(maxX));
    startY = Math.max(0, Math.floor(minY));
    endY = Math.min(h, Math.ceil(maxY));
  }

  const roiW = endX - startX;
  const roiH = endY - startY;
  if (roiW <= 0 || roiH <= 0) return;

  if (workersSupported && laserWorker) {
    if (state.laserWorkerBusy) return;
    state.laserWorkerBusy = true;
    try {
      const imgData = extractLaserRoiImageData(startX, startY, roiW, roiH, w, h);
      const buf = imgData.data.buffer;
      laserWorker.postMessage({
        type: "detect",
        payload: {
          buffer: buf,
          startX, endX, startY, endY,
          threshold: state.threshold,
          purity: state.colorPurity,
          lastShotTimestamp: state.lastShotTimestamp,
          cooldownMs: state.cooldownMs,
          now: now
        }
      }, [buf]);
      return;
    } catch (err) {
      console.warn("Fallback Laser para main thread:", err);
      state.laserWorkerBusy = false;
    }
  }

  // Fallback no main thread com edge-trigger para cadencias rapidas (150ms-220ms+) em 15 FPS VGA
  const imgData = extractLaserRoiImageData(startX, startY, roiW, roiH, w, h);
  const data = imgData.data;
  const gridW = Math.ceil(roiW / 2);
  const gridH = Math.ceil(roiH / 2);
  const gridLen = gridW * gridH * 3;

  if (!state.laserBgBuffer || state.laserBgW !== gridW || state.laserBgH !== gridH || state.laserBgBuffer.length !== gridLen) {
    state.laserBgBuffer = new Float32Array(gridLen);
    state.laserBgW = gridW;
    state.laserBgH = gridH;
    state.laserWasActive = false;
    let gIdx = 0;
    for (let y = 0; y < roiH; y += 2) {
      const rowOffset = y * roiW * 4;
      for (let x = 0; x < roiW; x += 2) {
        const idx = rowOffset + (x * 4);
        state.laserBgBuffer[gIdx] = data[idx];
        state.laserBgBuffer[gIdx + 1] = data[idx + 1];
        state.laserBgBuffer[gIdx + 2] = data[idx + 2];
        gIdx += 3;
      }
    }
    return;
  }

  const rawThresh = Number(state.threshold) || 215;
  const rawPurity = Number(state.colorPurity) || 42;
  const effThresh = Math.max(115, Math.min(235, rawThresh - 22));
  const effPurity = Math.max(10, Math.min(45, rawPurity * 0.55));
  const minDeltaR = Math.max(12, Math.min(26, (rawThresh - 120) * 0.16));

  let weightedX = 0;
  let weightedY = 0;
  let totalWeight = 0;
  let hitCount = 0;
  let minHitX = roiW;
  let maxHitX = -1;
  let minHitY = roiH;
  let maxHitY = -1;
  let gIdx = 0;
  const bg = state.laserBgBuffer;

  for (let y = 0; y < roiH; y += 2) {
    const rowOffset = y * roiW * 4;
    for (let x = 0; x < roiW; x += 2) {
      const idx = rowOffset + (x * 4);
      const r = data[idx];
      const g = data[idx + 1];
      const b = data[idx + 2];

      const prevR = bg[gIdx];
      const prevG = bg[gIdx + 1];
      const prevB = bg[gIdx + 2];

      const deltaR = r - prevR;
      const deltaG = g - prevG;
      const deltaB = b - prevB;
      const rgDiff = r - g;
      const rbDiff = r - b;

      const isRedLaser = (
        r >= effThresh &&
        rgDiff >= effPurity &&
        rbDiff >= effPurity * 0.75 &&
        deltaR >= minDeltaR &&
        (deltaR - deltaG) >= 5
      );

      const isDarkZoneLaser = (
        r >= 90 &&
        deltaR >= 24 &&
        rgDiff >= 16 &&
        rbDiff >= 16 &&
        (deltaR - deltaG) >= 12 &&
        (deltaR - deltaB) >= 12
      );

      const isSaturatedCore = (
        r >= 238 &&
        deltaR >= 16 &&
        rgDiff >= 14 &&
        rbDiff >= 16 &&
        (deltaR - deltaG) >= 14 &&
        (deltaR - deltaB) >= 14
      );

      if (isRedLaser || isDarkZoneLaser || isSaturatedCore) {
        const weight = Math.max(1, deltaR + rgDiff);
        weightedX += (startX + x) * weight;
        weightedY += (startY + y) * weight;
        totalWeight += weight;
        hitCount++;
        if (x < minHitX) minHitX = x;
        if (x > maxHitX) maxHitX = x;
        if (y < minHitY) minHitY = y;
        if (y > maxHitY) maxHitY = y;
      } else {
        const alpha = (deltaR > 8 && rgDiff > 8) ? 0.05 : 0.18;
        const keep = 1 - alpha;
        bg[gIdx] = prevR * keep + r * alpha;
        bg[gIdx + 1] = prevG * keep + g * alpha;
        bg[gIdx + 2] = prevB * keep + b * alpha;
      }

      gIdx += 3;
    }
  }

  const maxSpotSpan = Math.max(48, Math.round(Math.min(roiW, roiH) * 0.25));
  const spanX = maxHitX - minHitX;
  const spanY = maxHitY - minHitY;
  const isCompactCluster = hitCount >= 3 && hitCount <= 1500 && spanX <= maxSpotSpan && spanY <= maxSpotSpan && ((spanX <= 16 && spanY <= 16) || hitCount >= 6);

  if (isCompactCluster && totalWeight > 0) {
    const rawCamX = Math.round(weightedX / totalWeight);
    const rawCamY = Math.round(weightedY / totalWeight);
    const minSpotDist = Math.max(18, Math.round(roiW * 0.05));
    const dt = state.lastShotTimestamp > 0 ? (now - state.lastShotTimestamp) : 9999;
    const dist = Math.hypot(rawCamX - state.lastLaserTriggerX, rawCamY - state.lastLaserTriggerY);
    const isSameSpotPulse = state.laserWasActive && dist < minSpotDist && dt < 200;
    const isNewRapidDifferentSpot = dist >= minSpotDist && dt >= 120;
    const shouldFire = (dt >= state.cooldownMs && !isSameSpotPulse) || isNewRapidDifferentSpot;

    state.laserWasActive = true;
    if (shouldFire) {
      state.lastLaserTriggerX = rawCamX;
      state.lastLaserTriggerY = rawCamY;
      registrarDisparo(rawCamX, rawCamY, now);
    }
  } else {
    state.laserWasActive = false;
  }
}

function registrarDisparo(camX, camY, now) {
  let targetX, targetY;

  if (state.homographyMatrix) {
    const projected = Homography.projectPoint(state.homographyMatrix, { x: camX, y: camY });
    targetX = Math.round(projected.x);
    targetY = Math.round(projected.y);

    if (targetX < -20 || targetX > state.targetWidth + 20 || targetY < -20 || targetY > state.targetHeight + 20) {
      return;
    }
  } else {
    const cw = cameraCanvas.width;
    const ch = cameraCanvas.height;
    const normX = Math.max(0, Math.min(1, (camX - cw * 0.14) / (cw * 0.72)));
    const normY = Math.max(0, Math.min(1, (camY - ch * 0.12) / (ch * 0.76)));
    targetX = Math.round(normX * state.targetWidth);
    targetY = Math.round(normY * state.targetHeight);
  }

  recordTargetPlaneShot(targetX, targetY, camX, camY, now);
}

function syncSimpleShotUI(lastShot = null) {
  const count = state.shots.length;
  const label = `${count} DISPAROS`;
  if (badgeShots) badgeShots.textContent = label;
  if (targetShotsInline) targetShotsInline.textContent = label;
  if (statTotalShots) statTotalShots.textContent = count;

  if (lastShot) {
    const coord = `X:${lastShot.x} Y:${lastShot.y}`;
    if (targetLastXy) targetLastXy.textContent = `Último: ${coord}`;
    if (statLastCoord) statLastCoord.textContent = coord;
    if (statSplitTime) statSplitTime.textContent = `${lastShot.split} s`;
  } else {
    if (targetLastXy) targetLastXy.textContent = "Último: --";
    if (statLastCoord) statLastCoord.textContent = "--";
    if (statSplitTime) statSplitTime.textContent = "-- s";
    if (statExtremeSpread) statExtremeSpread.textContent = "-- px";
    if (statMeanRadius) statMeanRadius.textContent = "-- px";
  }
}

/**
 * Registra e projeta um impacto no plano canônico 600x800 do alvo.
 */
function recordTargetPlaneShot(targetX, targetY, camX = targetX, camY = targetY, now = performance.now()) {
  const clampedX = Math.max(0, Math.min(state.targetWidth, Math.round(targetX)));
  const clampedY = Math.max(0, Math.min(state.targetHeight, Math.round(targetY)));

  const splitTime = state.shots.length > 0
    ? ((now - state.lastShotTimestamp) / 1000).toFixed(2)
    : "0.00";

  state.lastShotTimestamp = now;

  const shot = {
    id: state.shots.length + 1,
    x: clampedX,
    y: clampedY,
    camX: camX,
    camY: camY,
    split: splitTime,
    timestamp: now
  };

  state.shots.push(shot);
  if (window.TargetAnalytics) {
    window.TargetAnalytics.enrichAndRecordShot(shot, state.shots);
  }
  playHitBeep(1800, 70);

  if (state.homographyMatrix) {
    spawnHitAnimation(clampedX, clampedY);
  }

  syncSimpleShotUI(shot);
  updateBallisticMetrics();
  renderShotsTable();

  diagnosticsPanel.textContent = `Tiro #${shot.id} em (${clampedX}, ${clampedY}) | Split: ${splitTime}s`;

  redrawTargetView();
}

// ========================================================
// 8. Gerenciamento de Camera e Loop Principal
// ========================================================
async function listCameras() {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const videoDevices = devices.filter(d => d.kind === "videoinput");

    cameraSelect.innerHTML = "";
    if (videoDevices.length === 0) {
      const opt = document.createElement("option");
      opt.text = "Camera Traseira (Padrao)";
      opt.value = "";
      cameraSelect.appendChild(opt);
      return;
    }

    videoDevices.forEach((device, index) => {
      const opt = document.createElement("option");
      opt.value = device.deviceId;
      const label = device.label || `Camera ${index + 1}`;
      opt.text = label;
      if (/back|traseira|environment/i.test(label) && !state.activeDeviceId) {
        opt.selected = true;
        state.activeDeviceId = device.deviceId;
      }
      cameraSelect.appendChild(opt);
    });

    if (!state.activeDeviceId && videoDevices.length > 0) {
      state.activeDeviceId = videoDevices[0].deviceId;
    }
  } catch (err) {
    console.error("Erro ao listar cameras:", err);
  }
}

function getDesiredResolution() {
  const raw = state.resolutionKey || resolutionSelect?.value || "640x480";
  const parts = raw.split("x");
  const w = parseInt(parts[0], 10) || 640;
  const h = parseInt(parts[1], 10) || 480;
  return { width: w, height: h };
}

function syncZoomControlsDisabled(disabled) {
  if (cameraZoomSlider) cameraZoomSlider.disabled = Boolean(disabled);
  zoomPresetButtons.forEach(btn => {
    btn.disabled = Boolean(disabled);
  });
}

function syncZoomUI(zoomValue) {
  const z = Number(zoomValue) || 1.0;
  if (cameraZoomVal) cameraZoomVal.textContent = `${z.toFixed(1)}x`;
  if (cameraZoomSlider) cameraZoomSlider.value = z.toFixed(1);
  zoomPresetButtons.forEach(btn => {
    const btnZ = parseFloat(btn.getAttribute("data-zoom"));
    btn.classList.toggle("active", Math.abs(btnZ - z) < 0.08);
  });
}

async function applyCameraZoom(requestedZoom) {
  if (state.isTargetLocked) return;
  const z = Math.max(1.0, Math.min(4.0, Math.round((Number(requestedZoom) || 1.0) * 10) / 10));
  state.zoomLevel = z;
  syncZoomUI(z);

  let hwZoom = 1.0;
  if (state.currentStream) {
    try {
      const track = state.currentStream.getVideoTracks()[0];
      const caps = track && typeof track.getCapabilities === "function" ? track.getCapabilities() : null;
      if (caps && caps.zoom && typeof caps.zoom.max === "number" && caps.zoom.max > (caps.zoom.min || 1)) {
        const minZ = Math.max(1.0, Number(caps.zoom.min) || 1.0);
        const maxZ = Number(caps.zoom.max);
        const hwTarget = Math.min(maxZ, Math.max(minZ, z));
        await track.applyConstraints({ advanced: [{ zoom: hwTarget }] });
        const applied = typeof track.getSettings === "function" ? track.getSettings().zoom : hwTarget;
        hwZoom = applied && applied >= minZ ? applied : hwTarget;
      }
    } catch (_) {
      hwZoom = 1.0;
    }
  }

  state.hwZoom = hwZoom;
  state.digitalZoom = Math.max(1.0, z / Math.max(1.0, hwZoom));
  state.markerCache.clear();
  state.autoLockStartTime = 0;
}

async function applyCameraHardwareLock(locked) {
  if (!state.currentStream) return;
  try {
    const track = state.currentStream.getVideoTracks()[0];
    if (!track || typeof track.getCapabilities !== "function") return;
    const caps = track.getCapabilities();
    const adv = {};

    if (Array.isArray(caps.focusMode)) {
      if (locked && caps.focusMode.includes("manual")) {
        adv.focusMode = "manual";
      } else if (!locked && caps.focusMode.includes("continuous")) {
        adv.focusMode = "continuous";
      }
    }
    if (Array.isArray(caps.whiteBalanceMode)) {
      if (locked && caps.whiteBalanceMode.includes("manual")) {
        adv.whiteBalanceMode = "manual";
      } else if (!locked && caps.whiteBalanceMode.includes("continuous")) {
        adv.whiteBalanceMode = "continuous";
      }
    }
    if (Array.isArray(caps.exposureMode)) {
      if (locked && caps.exposureMode.includes("manual")) {
        adv.exposureMode = "manual";
      } else if (!locked && caps.exposureMode.includes("continuous")) {
        adv.exposureMode = "continuous";
      }
    }

    if (Object.keys(adv).length > 0) {
      await track.applyConstraints({ advanced: [adv] });
    }
  } catch (_) {}

  await applyHardwareExposure(locked ? state.exposureDimming : 0);
}

async function _openCameraStream(deviceId, res, targetFps) {
  const buildConstraints = (strictMinFps) => {
    const videoCfg = {
      width: { ideal: res.width },
      height: { ideal: res.height },
      frameRate: strictMinFps
        ? { ideal: targetFps, min: targetFps >= 30 ? 24 : 10 }
        : { ideal: targetFps }
    };
    if (deviceId) {
      videoCfg.deviceId = { exact: deviceId };
    } else {
      videoCfg.facingMode = { ideal: "environment" };
    }
    return { audio: false, video: videoCfg };
  };

  try {
    return await navigator.mediaDevices.getUserMedia(buildConstraints(true));
  } catch (err) {
    if (err && (err.name === "OverconstrainedError" || err.name === "ConstraintNotSatisfiedError")) {
      return await navigator.mediaDevices.getUserMedia(buildConstraints(false));
    }
    throw err;
  }
}

function updateHardwareTelemetry(stream, targetFps) {
  const track = stream && stream.getVideoTracks ? stream.getVideoTracks()[0] : null;
  const settings = track && typeof track.getSettings === "function" ? track.getSettings() : {};
  const hwFps = settings.frameRate ? Math.round(settings.frameRate) : 0;
  state.hwFrameRate = hwFps;

  const w = cameraCanvas.width || settings.width || 640;
  const h = cameraCanvas.height || settings.height || 480;
  cameraResLabel.textContent = hwFps > 0
    ? `${w}x${h} @ ${hwFps}Hz`
    : `${w}x${h} @ ${targetFps}p`;

  if (hwFps > 0 && hwFps < 22 && targetFps >= 30 && w >= 1280) {
    diagnosticsPanel.textContent = `Câmera entregando ${hwFps} FPS em ${w}x${h}. Dica: selecione 960x540 ou 640x480 em Ajustes para liberar mais FPS.`;
  } else {
    diagnosticsPanel.textContent = `Camera ativa: ${w}x${h}${hwFps ? ` (${hwFps} FPS hardware)` : ""}. Aponte para o alvo.`;
  }
}

async function startCamera(deviceId = null) {
  stopCamera();

  const cameraWrapper = document.querySelector("#panel-camera .canvas-wrapper");
  if (cameraWrapper) cameraWrapper.classList.add("loading");
  updateStatusBadge(false, "CONECTANDO...");

  const targetFps = state.targetFps || 15;
  const desiredRes = getDesiredResolution();
  state.laserIntervalMs = targetFps >= 60 ? 0 : Math.max(15, Math.round(1000 / targetFps) - 4);
  state.arucoIntervalMs = Math.max(100, Math.round(1000 / Math.min(targetFps, 10)));
  if (cameraFpsVal) cameraFpsVal.textContent = `0 FPS (Alvo: ${targetFps} FPS)`;
  if (cameraArucoState) cameraArucoState.textContent = "BUSCANDO 4 ARUCOS";

  try {
    diagnosticsPanel.textContent = `Conectando sensor ${desiredRes.width}x${desiredRes.height} (${targetFps} FPS)...`;
    const stream = await _openCameraStream(deviceId, desiredRes, targetFps);
    state.currentStream = stream;

    await new Promise((resolve) => {
      let resolved = false;
      const done = () => {
        if (resolved) return;
        resolved = true;
        resolve();
      };
      video.onloadedmetadata = done;
      video.srcObject = stream;
      if (video.readyState >= 1) done();
      setTimeout(done, 1500);
    });
    await video.play().catch(() => {});

    state.isStreaming = true;
    state.lastProcessedVideoTime = -1;
    state.lastRenderTimestamp = 0;
    state.lastFrameTime = performance.now();
    state.frameCount = 0;
    state.arucoWorkerBusy = false;
    state.laserWorkerBusy = false;

    if (cameraPermissionGate) cameraPermissionGate.style.display = "none";
    if (cameraLiveArea) cameraLiveArea.style.display = "flex";

    updateStatusBadge(true, "CAMERA ATIVA");
    if (btnToggleCamera) {
      btnToggleCamera.textContent = "Parar Camera";
      btnToggleCamera.classList.add("recording");
    }

    cameraCanvas.width = video.videoWidth || desiredRes.width;
    cameraCanvas.height = video.videoHeight || desiredRes.height;
    updateHardwareTelemetry(stream, targetFps);
    await applyCameraZoom(state.zoomLevel || 1.0);
    syncZoomControlsDisabled(false);

    if (cameraWrapper) cameraWrapper.classList.remove("loading");
    await listCameras();

    if (!workersSupported) {
      initWorkers();
    }

    startVideoCaptureLoop();
  } catch (err) {
    console.error("Erro camera:", err);
    if (cameraWrapper) cameraWrapper.classList.remove("loading");
    updateStatusBadge(false, "PERMISSAO NEGADA");
    diagnosticsPanel.textContent = `Acesso a camera negado: ${err.message}`;
  }
}

async function applyCameraFpsProfile(newFps) {
  state.targetFps = newFps;
  state.laserIntervalMs = newFps >= 60 ? 0 : Math.max(15, Math.round(1000 / newFps) - 4);
  state.arucoIntervalMs = Math.max(100, Math.round(1000 / Math.min(newFps, 10)));
  if (cameraFpsVal) cameraFpsVal.textContent = `${state.currentFps} FPS (Alvo: ${newFps} FPS)`;

  if (state.currentStream) {
    try {
      const track = state.currentStream.getVideoTracks()[0];
      if (track && typeof track.applyConstraints === "function") {
        await track.applyConstraints({
          frameRate: newFps >= 30
            ? { ideal: newFps, min: Math.min(newFps, 24) }
            : { ideal: newFps, max: newFps }
        });
        updateHardwareTelemetry(state.currentStream, newFps);
        return;
      }
    } catch (_) {
      startCamera(cameraSelect.value || state.activeDeviceId);
    }
  }
}

async function applyCameraResolution(resKey) {
  state.resolutionKey = resKey || "640x480";
  if (state.isStreaming) {
    await startCamera(cameraSelect.value || state.activeDeviceId);
  }
}

async function applyHardwareExposure(dimmingPercent) {
  if (!state.currentStream) return;
  try {
    const track = state.currentStream.getVideoTracks()[0];
    if (!track || typeof track.getCapabilities !== "function") return;
    const caps = track.getCapabilities();
    const advanced = {};

    if (caps.exposureCompensation) {
      const minExp = typeof caps.exposureCompensation.min === "number" ? caps.exposureCompensation.min : -2;
      const maxExp = typeof caps.exposureCompensation.max === "number" ? caps.exposureCompensation.max : 0;
      const neutralExp = Math.max(minExp, Math.min(maxExp, 0));
      const clampedDim = Math.max(0, Math.min(100, Number(dimmingPercent) || 0));
      const targetExp = neutralExp - (clampedDim / 100) * (neutralExp - minExp);
      advanced.exposureCompensation = Math.max(minExp, Math.min(maxExp, targetExp));
    }
    if (Object.keys(advanced).length > 0) {
      await track.applyConstraints({ advanced: [advanced] });
    }
  } catch (_) {}
}

function stopCamera() {
  if (state.videoFrameCallbackId && typeof video.cancelVideoFrameCallback === "function") {
    try {
      video.cancelVideoFrameCallback(state.videoFrameCallbackId);
    } catch (_) {}
    state.videoFrameCallbackId = null;
  }
  if (state.currentStream) {
    state.currentStream.getTracks().forEach(t => t.stop());
    state.currentStream = null;
  }
  state.isStreaming = false;
  state.renderLoopRunning = false;
  state.currentFps = 0;
  state.hwFrameRate = 0;
  if (badgeFps) badgeFps.textContent = "0 FPS";
  if (cameraFpsVal) cameraFpsVal.textContent = `0 FPS (Alvo: ${state.targetFps || 15} FPS)`;
  updateStatusBadge(false, "AGUARDANDO");
  if (btnToggleCamera) {
    btnToggleCamera.textContent = "Ligar Camera";
    btnToggleCamera.classList.remove("recording");
  }
}

function updateStatusBadge(active, text) {
  badgeStatus.textContent = text;
  if (statusDot) {
    statusDot.classList.toggle("online", Boolean(active));
  }
}

function applyViewMode(mode) {
  if (mode === "target") {
    switchTab("tab-alvo");
  } else if (mode === "camera" || mode === "searching") {
    switchTab("tab-camera");
  }
}

function processCameraFrame(now, forceNewFrame = false) {
  if (video.paused && state.currentStream) {
    video.play().catch(() => {});
  }

  const targetFps = state.targetFps || 15;
  const slackMs = targetFps <= 15 ? 10 : 5;
  const minFrameInterval = targetFps >= 60 ? 0 : Math.max(0, (1000 / targetFps) - slackMs);
  if (minFrameInterval > 0 && (now - state.lastRenderTimestamp) < minFrameInterval) {
    return;
  }

  const currentVideoTime = video.currentTime;
  const hasNewVideoFrame = forceNewFrame || (currentVideoTime !== state.lastProcessedVideoTime) || (now - state.lastRenderTimestamp > Math.max(30, minFrameInterval));
  if (!hasNewVideoFrame || video.readyState < 2) {
    return;
  }

  state.lastProcessedVideoTime = currentVideoTime;
  state.lastRenderTimestamp = now;

  // Calculo de FPS Real Processado
  state.frameCount++;
  const elapsed = now - state.lastFrameTime;
  if (elapsed >= 1000) {
    state.currentFps = Math.round((state.frameCount * 1000) / elapsed);
    if (badgeFps) badgeFps.textContent = `${state.currentFps} FPS`;
    if (cameraFpsVal) cameraFpsVal.textContent = `${state.currentFps} FPS (Alvo: ${targetFps} FPS)`;
    state.frameCount = 0;
    state.lastFrameTime = now;
  }

  // So copia o frame completo quando buscando ArUcos ou com aba Camera aberta
  const needsFullCameraDraw = !state.isTargetLocked || document.body.classList.contains("tab-camera");
  if (needsFullCameraDraw) {
    drawCameraView(cameraCtx, cameraCanvas.width, cameraCanvas.height);
  }

  if (!state.isTargetLocked && !state.manualCalibrationMode) {
    if (now - state.lastArucoCheckTime >= state.arucoIntervalMs) {
      state.lastArucoCheckTime = now;
      processTargetDetection(now);
    }
    drawTargetTrackingOverlay();
  } else if (state.isTargetLocked) {
    detectLaserImpulse(now);
  }
}

function startVideoCaptureLoop() {
  state.renderLoopRunning = true;
  const supportsRvfc = typeof video.requestVideoFrameCallback === "function";

  if (supportsRvfc) {
    const onVideoFrame = (now) => {
      if (!state.isStreaming) {
        state.renderLoopRunning = false;
        state.videoFrameCallbackId = null;
        return;
      }
      try {
        processCameraFrame(now, true);
        updateHitAnimations(now);
      } catch (err) {
        console.error("Erro recuperado no videoFrameCallback:", err);
      }
      state.videoFrameCallbackId = video.requestVideoFrameCallback(onVideoFrame);
    };
    state.videoFrameCallbackId = video.requestVideoFrameCallback(onVideoFrame);
    return;
  }

  requestAnimationFrame(renderLoop);
}

// Loop Fallback rAF (navegadores sem requestVideoFrameCallback)
function renderLoop(now) {
  if (!state.isStreaming) {
    state.renderLoopRunning = false;
    return;
  }
  state.renderLoopRunning = true;

  try {
    processCameraFrame(now, false);
    updateHitAnimations(now);
  } catch (err) {
    console.error("Erro recuperado no renderLoop:", err);
  }

  requestAnimationFrame(renderLoop);
}

// ========================================================
// 9. Eventos de Interface e Calibracao Manual
// ========================================================
cameraCanvas.addEventListener("pointerdown", (e) => {
  if (!state.manualCalibrationMode) return;

  const rect = cameraCanvas.getBoundingClientRect();
  const scaleX = cameraCanvas.width / rect.width;
  const scaleY = cameraCanvas.height / rect.height;
  const clickX = Math.round((e.clientX - rect.left) * scaleX);
  const clickY = Math.round((e.clientY - rect.top) * scaleY);

  state.manualPoints.push({ x: clickX, y: clickY });
  playHitBeep(1400, 50);

  const labels = ["Superior Esquerdo (TL)", "Superior Direito (TR)", "Inferior Direito (BR)", "Inferior Esquerdo (BL)"];
  if (state.manualPoints.length < 4) {
    diagnosticsPanel.textContent = `Ponto ${state.manualPoints.length}/4 marcado. Clique no canto: ${labels[state.manualPoints.length]}`;
  } else {
    // 4 pontos clicados -> Trava o alvo!
    const corners = {
      tl: state.manualPoints[0],
      tr: state.manualPoints[1],
      br: state.manualPoints[2],
      bl: state.manualPoints[3],
      modelName: "CALIBRACAO MANUAL"
    };
    state.manualCalibrationMode = false;
    lockTargetWithCorners(corners);
    diagnosticsPanel.textContent = "Alvo calibrado manualmente com sucesso!";
  }
});

btnAuthorizeCamera?.addEventListener("click", () => {
  startCamera(cameraSelect.value);
});

btnToggleCamera?.addEventListener("click", () => {
  if (state.isStreaming) {
    stopCamera();
  } else {
    startCamera(cameraSelect.value);
  }
});

btnRecalibrate?.addEventListener("click", () => {
  unlockTarget();
});

btnManualCalibrate?.addEventListener("click", () => {
  unlockTarget();
  state.manualCalibrationMode = true;
  state.manualPoints = [];
  applyViewMode("camera");
  diagnosticsPanel.textContent = "MODO MANUAL: Clique nos 4 cantos do papel na tela (1: Top-Left, 2: Top-Right, 3: Bottom-Right, 4: Bottom-Left).";
  playHitBeep(1200, 80);
});

btnFlip?.addEventListener("click", () => {
  state.isFlipped = !state.isFlipped;
  btnFlip.classList.toggle("active", state.isFlipped);
  if (chkFlip) chkFlip.checked = state.isFlipped;
});

chkFlip?.addEventListener("change", (e) => {
  state.isFlipped = Boolean(e.target.checked);
  btnFlip?.classList.toggle("active", state.isFlipped);
});

darkModeToggle?.addEventListener("change", (e) => {
  document.body.classList.toggle("dark-mode", Boolean(e.target.checked));
  redrawTargetView();
  window.TargetAnalytics?.onThemeChange();
  saveSession();
});

btnBeepTest?.addEventListener("click", () => {
  playHitBeep(1600, 100);
});

function redrawTargetView() {
  if (state.homographyMatrix) {
    warpTargetView();
  } else {
    drawFallbackTarget();
    state.shots.forEach((s, idx) => drawBulletHole(targetCtx, s.x, s.y, idx + 1));
  }
}

function clearAllShots() {
  state.shots = [];
  syncSimpleShotUI(null);
  diagnosticsPanel.textContent = "Série zerada. Alvo pronto.";
  renderShotsTable();
  redrawTargetView();
  window.TargetAnalytics?.syncShotsFromApp(state.shots);
  saveSession();
  playHitBeep(800, 120);
}

btnClearShots?.addEventListener("click", clearAllShots);
btnClearShotsStats?.addEventListener("click", clearAllShots);

document.querySelectorAll(".nav-item[data-target], .nav-desktop-btn[data-target]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = btn.getAttribute("data-target");
    if (target) switchTab("tab-" + target.replace(/^tab-/, ""));
  });
});

cameraSelect?.addEventListener("change", (e) => {
  if (state.isStreaming) {
    startCamera(e.target.value);
  }
});

fpsSelect?.addEventListener("change", (e) => {
  const selectedFps = parseInt(e.target.value, 10) || 15;
  applyCameraFpsProfile(selectedFps);
});

resolutionSelect?.addEventListener("change", (e) => {
  applyCameraResolution(e.target.value);
});

cameraZoomSlider?.addEventListener("input", (e) => {
  applyCameraZoom(parseFloat(e.target.value));
});

zoomPresetButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    const z = parseFloat(btn.getAttribute("data-zoom"));
    if (!Number.isNaN(z)) {
      applyCameraZoom(z);
    }
  });
});

sliderThreshold?.addEventListener("input", (e) => {
  state.threshold = parseInt(e.target.value, 10);
  thresholdVal.textContent = state.threshold;
});

sliderPurity?.addEventListener("input", (e) => {
  state.colorPurity = parseInt(e.target.value, 10);
  purityVal.textContent = state.colorPurity;
});

sliderExposure?.addEventListener("input", (e) => {
  state.exposureDimming = parseInt(e.target.value, 10) || 0;
  if (exposureVal) exposureVal.textContent = `${state.exposureDimming}%`;
  if (state.isTargetLocked && state.isStreaming) {
    state.laserWarmupUntil = performance.now() + 250;
    state.laserBgBuffer = null;
    try {
      laserWorker?.postMessage({ type: "reset-bg" });
    } catch (_) {}
    applyHardwareExposure(state.exposureDimming);
  }
});

tabPip?.addEventListener("click", () => applyViewMode("pip"));
tabTarget?.addEventListener("click", () => applyViewMode("target"));
tabCamera?.addEventListener("click", () => applyViewMode("camera"));

// Inicializacao sem ligar a camera automaticamente (Aguarda permissao explicita no Card da Aba 1)
loadSession();
window.TargetAnalytics?.initUI({
  onClearShots: () => {
    state.shots = [];
    syncSimpleShotUI(null);
    renderShotsTable();
    redrawTargetView();
    saveSession();
  },
  onRedrawTarget: () => redrawTargetView(),
  onRescanArea: () => {
    const modelName = state.targetCorners ? state.targetCorners.modelName : null;
    window.TargetAnalytics?.scanTargetArea(targetCtx, state.targetWidth, state.targetHeight, modelName, true);
    redrawTargetView();
  }
});
window.TargetAnalytics?.syncShotsFromApp(state.shots);
syncSimpleShotUI(null);
renderShotsTable();
redrawTargetView();
switchTab(document.body.classList.contains("tab-alvo") ? "tab-alvo" : "tab-camera");
listCameras();
