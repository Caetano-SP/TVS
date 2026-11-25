/**
 * ArUco Marker Detection Web Worker
 * Executa deteccao estrita (4x4, 5x5, 6x6, 7x7) em paridade com cv2.aruco.detectMarkers
 * usando Transferable ping-pong (devolve o buffer para reciclagem sem churn de GC).
 */

try {
  importScripts("../lib/aruco_dictionaries.js", "../lib/aruco_universal.js");
} catch (e1) {
  try {
    importScripts("/lib/aruco_dictionaries.js", "/lib/aruco_universal.js");
  } catch (e2) {
    console.error("Falha ao carregar scripts do ArUco no Worker:", e2);
  }
}

const detector = typeof ArUcoUniversalDetector !== "undefined" ? new ArUcoUniversalDetector() : null;

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

  switch (type) {
    case "detect": {
      const buf = payload && payload.buffer instanceof ArrayBuffer ? payload.buffer : null;
      if (!detector || !payload || !buf) {
        replyWithBuffer({
          type: "detect-result",
          payload: { markers: [], corners: null, markerCache: [] }
        }, buf);
        return;
      }

      const { width, height, targetSize, markerCache, markerCacheTtlMs, now } = payload;
      const rgbaData = new Uint8ClampedArray(buf);
      const rawMarkers = detector.detectMarkersDownsampled(rgbaData, width, height, targetSize);

      // Se o frame atual ja trouxe um quarteto valido, usa diretamente sem misturar cache antigo
      const directCorners = ArUcoUniversalDetector.identifyTargetCorners(rawMarkers, width, height);
      if (directCorners) {
        const freshCache = new Map();
        rawMarkers.forEach(m => {
          freshCache.set(`${m.dictionary}:${m.id}`, { marker: m, lastSeen: now });
        });
        replyWithBuffer({
          type: "detect-result",
          payload: {
            markers: rawMarkers,
            corners: directCorners,
            rawCount: rawMarkers.length,
            markerCache: Array.from(freshCache.entries())
          }
        }, buf);
        return;
      }

      // Persistencia curta (200ms) apenas quando ha >= 2 marcadores reais no frame atual e mesmo dicionario
      const updatedCache = new Map(rawMarkers.length >= 2 ? (markerCache || []) : []);
      if (rawMarkers.length > 0) {
        const dominantDict = rawMarkers[0].dictionary;
        for (const [key, entry] of updatedCache.entries()) {
          if (!entry || !entry.marker || entry.marker.dictionary !== dominantDict || (now - entry.lastSeen > markerCacheTtlMs)) {
            updatedCache.delete(key);
          }
        }
        rawMarkers.forEach(m => {
          if (m.dictionary === dominantDict) {
            updatedCache.set(`${m.dictionary}:${m.id}`, { marker: m, lastSeen: now });
          }
        });
      }

      const markers = rawMarkers.length >= 2
        ? Array.from(updatedCache.values()).map(entry => entry.marker)
        : rawMarkers;
      const corners = ArUcoUniversalDetector.identifyTargetCorners(markers, width, height);

      replyWithBuffer({
        type: "detect-result",
        payload: {
          markers,
          corners,
          rawCount: rawMarkers.length,
          markerCache: Array.from(updatedCache.entries())
        }
      }, buf);
      break;
    }

    case "identify": {
      const { markers, width, height } = payload;
      const corners = typeof ArUcoUniversalDetector !== "undefined"
        ? ArUcoUniversalDetector.identifyTargetCorners(markers, width || 0, height || 0)
        : null;
      self.postMessage({
        type: "identify-result",
        payload: { corners }
      });
      break;
    }
  }
};
