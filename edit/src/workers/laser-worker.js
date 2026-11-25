/**
 * Laser Detection Web Worker
 * Combina subtração temporal de fundo (Delta Frame) + dominância espectral do laser
 * com detecção por borda de subida (edge-trigger) e refratário espacial para capturar
 * cadências rápidas de 150ms a 220ms+, mantendo reciclagem ping-pong de ArrayBuffer.
 */

let bgBuffer = null;
let bgWidth = 0;
let bgHeight = 0;

// Estado de borda do pulso para suportar cadencias rapidas (150ms - 220ms+) sem duplicar um tiro unico
let wasLaserActive = false;
let lastTriggerTime = 0;
let lastTriggerX = -9999;
let lastTriggerY = -9999;

function replyWithBuffer(msg, buffer) {
  if (buffer instanceof ArrayBuffer && buffer.byteLength > 0) {
    msg.payload.recycledBuffer = buffer;
    self.postMessage(msg, [buffer]);
  } else {
    self.postMessage(msg);
  }
}

self.onmessage = function(e) {
  const { type, payload } = e.data;

  if (type === 'reset-bg') {
    bgBuffer = null;
    bgWidth = 0;
    bgHeight = 0;
    wasLaserActive = false;
    lastTriggerTime = 0;
    lastTriggerX = -9999;
    lastTriggerY = -9999;
    return;
  }

  if (type === 'detect') {
    const {
      imageData, buffer,
      startX, endX, startY, endY,
      threshold, purity,
      lastShotTimestamp, cooldownMs, now
    } = payload || {};

    const transferBuf = buffer instanceof ArrayBuffer ? buffer : null;
    const roiW = endX - startX;
    const roiH = endY - startY;
    const data = transferBuf ? new Uint8ClampedArray(transferBuf) : (imageData && imageData.data);
    if (roiW <= 0 || roiH <= 0 || !data) {
      replyWithBuffer({ type: 'detect-result', payload: { hit: false } }, transferBuf);
      return;
    }

    const gridW = Math.ceil(roiW / 2);
    const gridH = Math.ceil(roiH / 2);
    const gridLen = gridW * gridH * 3;

    // Inicializa o buffer de fundo na primeira leitura desta ROI
    if (!bgBuffer || bgWidth !== gridW || bgHeight !== gridH || bgBuffer.length !== gridLen) {
      bgBuffer = new Float32Array(gridLen);
      bgWidth = gridW;
      bgHeight = gridH;
      wasLaserActive = false;
      let gIdx = 0;
      for (let y = 0; y < roiH; y += 2) {
        const rowOffset = y * roiW * 4;
        for (let x = 0; x < roiW; x += 2) {
          const idx = rowOffset + (x * 4);
          bgBuffer[gIdx] = data[idx];
          bgBuffer[gIdx + 1] = data[idx + 1];
          bgBuffer[gIdx + 2] = data[idx + 2];
          gIdx += 3;
        }
      }
      replyWithBuffer({ type: 'detect-result', payload: { hit: false } }, transferBuf);
      return;
    }

    // Mapeia os sliders de sensibilidade de forma tolerante e anti-ruido
    const rawThresh = Number(threshold) || 215;
    const rawPurity = Number(purity) || 42;
    const effThresh = Math.max(115, Math.min(235, rawThresh - 22));
    const effPurity = Math.max(10, Math.min(45, rawPurity * 0.55));
    const effPurity075 = effPurity * 0.75;
    const minDeltaR = Math.max(12, Math.min(26, (rawThresh - 120) * 0.16));
    const rowStride = roiW * 4;

    let weightedX = 0;
    let weightedY = 0;
    let totalWeight = 0;
    let hitCount = 0;
    let maxR = 0;
    let minHitX = roiW;
    let maxHitX = -1;
    let minHitY = roiH;
    let maxHitY = -1;
    let gIdx = 0;

    for (let y = 0; y < roiH; y += 2) {
      const rowOffset = y * rowStride;
      for (let x = 0; x < roiW; x += 2) {
        const idx = rowOffset + (x * 4);
        const r = data[idx];
        const prevR = bgBuffer[gIdx];
        const deltaR = r - prevR;

        // Early-skip: se r < 90 ou deltaR < 12, nenhuma das 3 condicoes de laser pode ser verdadeira
        if (r < 90 || deltaR < 12) {
          const g = data[idx + 1];
          const b = data[idx + 2];
          const alpha = (deltaR > 8 && (r - g) > 8) ? 0.05 : 0.18;
          const keep = 1 - alpha;
          bgBuffer[gIdx] = prevR * keep + r * alpha;
          bgBuffer[gIdx + 1] = bgBuffer[gIdx + 1] * keep + g * alpha;
          bgBuffer[gIdx + 2] = bgBuffer[gIdx + 2] * keep + b * alpha;
          gIdx += 3;
          continue;
        }

        const g = data[idx + 1];
        const b = data[idx + 2];
        const prevG = bgBuffer[gIdx + 1];
        const prevB = bgBuffer[gIdx + 2];

        const deltaG = g - prevG;
        const deltaB = b - prevB;
        const rgDiff = r - g;
        const rbDiff = r - b;

        // 1. Ponto vermelho puro com variação temporal positiva
        const isRedLaser = (
          r >= effThresh &&
          rgDiff >= effPurity &&
          rbDiff >= effPurity075 &&
          deltaR >= minDeltaR &&
          (deltaR - deltaG) >= 5
        );

        // 2. Pulso de laser sobre zona escura/preta do alvo (rejeita luz quente difusa)
        const isDarkZoneLaser = (
          deltaR >= 24 &&
          rgDiff >= 16 &&
          rbDiff >= 16 &&
          (deltaR - deltaG) >= 12 &&
          (deltaR - deltaB) >= 12
        );

        // 3. Núcleo super-brilhante saturado sobre papel branco claro (rejeita flicker neutro/quente)
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
          if (r > maxR) maxR = r;
          if (x < minHitX) minHitX = x;
          if (x > maxHitX) maxHitX = x;
          if (y < minHitY) minHitY = y;
          if (y > maxHitY) maxHitY = y;
        } else {
          // Desacelera absorção de halos vermelhos fracos para não mascarar o próximo disparo rápido no mesmo ponto
          const alpha = (deltaR > 8 && rgDiff > 8) ? 0.05 : 0.18;
          const keep = 1 - alpha;
          bgBuffer[gIdx] = prevR * keep + r * alpha;
          bgBuffer[gIdx + 1] = prevG * keep + g * alpha;
          bgBuffer[gIdx + 2] = prevB * keep + b * alpha;
        }

        gIdx += 3;
      }
    }

    const effectiveCooldown = Math.max(70, Number(cooldownMs) || 95);
    const minSpotDist = Math.max(18, Math.round(roiW * 0.05));
    const maxSpotSpan = Math.max(48, Math.round(Math.min(roiW, roiH) * 0.25));
    const spanX = maxHitX - minHitX;
    const spanY = maxHitY - minHitY;
    const isCompactCluster = hitCount >= 3 && hitCount <= 1500 && spanX <= maxSpotSpan && spanY <= maxSpotSpan && ((spanX <= 16 && spanY <= 16) || hitCount >= 6);

    if (isCompactCluster && totalWeight > 0) {
      const camX = Math.round(weightedX / totalWeight);
      const camY = Math.round(weightedY / totalWeight);

      const refTime = Math.max(Number(lastShotTimestamp) || 0, lastTriggerTime || 0);
      const dt = refTime > 0 ? (now - refTime) : 9999;
      const dist = Math.hypot(camX - lastTriggerX, camY - lastTriggerY);

      // Bloqueia continuação do mesmo pulso ou arrasto de gatilho (< 120ms)
      const isSameSpotPulse = wasLaserActive && dist < minSpotDist && dt < 200;
      const isNewRapidDifferentSpot = dist >= minSpotDist && dt >= 120;
      const shouldFire = (dt >= effectiveCooldown && !isSameSpotPulse) || isNewRapidDifferentSpot;

      wasLaserActive = true;

      if (shouldFire) {
        lastTriggerTime = now;
        lastTriggerX = camX;
        lastTriggerY = camY;
        replyWithBuffer({
          type: 'detect-result',
          payload: { hit: true, camX, camY, hitCount, maxR }
        }, transferBuf);
        return;
      }
    } else {
      // Laser apagou ou dispersão macroscópica de ruído -> rearma sem disparar
      wasLaserActive = false;
    }

    replyWithBuffer({ type: 'detect-result', payload: { hit: false } }, transferBuf);
  }
};
