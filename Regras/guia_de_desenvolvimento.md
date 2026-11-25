# Guia de Desenvolvimento: Sistema de Treino Dry-Fire (Estilo Mantis Laser Academy)

Este documento reúne a arquitetura completa e o roteiro técnico em 5 etapas para construir uma aplicação de treinamento a seco (*dry-fire*) com detecção ótica de disparos a laser em tempo real.

---

## Sumário
1. [Passo 1: O "Smart Target" com Marcadores Fiduciais (ArUco)](#passo-1-o-smart-target-com-marcadores-fiduciais-aruco)
2. [Passo 2: O Pipeline Matemático e Protótipo em Python](#passo-2-o-pipeline-matemático-e-protótipo-em-python)
3. [Passo 3: Portabilidade para o Android APK (CameraX + OpenCV)](#passo-3-portabilidade-para-o-android-apk-camerax--opencv)
4. [Passo 4: Shot Timer, Lógica de Treino e Feedback Sonoro](#passo-4-shot-timer-lógica-de-treino-e-feedback-sonoro)
5. [Passo 5: Hardware, Materiais e Checklist de Setup](#passo-5-hardware-materiais-e-checklist-de-setup)

---

## Passo 1: O "Smart Target" com Marcadores Fiduciais (ArUco)

A precisão do cálculo de impacto depende da eliminação de distorções de perspectiva causadas pelo ângulo entre o smartphone e a parede. O método mais robusto e idêntico ao do Mantis consiste no uso de marcadores fiduciais ArUco nas extremidades do alvo.

### 1.1 Configuração dos Marcadores
Utilize o dicionário predefinido `cv2.aruco.DICT_4X4_50` e gere quatro marcadores distintos:
* **ID 0:** Canto superior esquerdo $(0, 0)$
* **ID 1:** Canto superior direito $(W, 0)$
* **ID 2:** Canto inferior direito $(W, H)$
* **ID 3:** Canto inferior esquerdo $(0, H)$

### 1.2 Retificação de Perspectiva (Homografia 2D)
A transformação planar converte os quatro pontos detectados na câmera $\mathbf{p}_i = (x_i, y_i)$ para o plano ideal retificado $\mathbf{p}'_i = (x'_i, y'_i)$:

$$\mathbf{p}' = \mathbf{H} \mathbf{p}$$

Onde $\mathbf{H}$ é a matriz de homografia $3 \times 3$ obtida via `cv2.getPerspectiveTransform()`. Toda a análise de pontuação e detecção luminosa será executada sobre essa imagem retificada com resolução fixa (ex: $600 \times 800$ pixels).

---

## Passo 2: O Pipeline Matemático e Protótipo em Python

Antes de compilar no dispositivo móvel, o pipeline deve ser validado localmente no computador via webcam com um script funcional.

```python
import cv2
import numpy as np
import time

TARGET_W, TARGET_H = 600, 800
CENTER_X, CENTER_Y = TARGET_W // 2, TARGET_H // 2

# Definição do dicionário ArUco
aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
aruco_params = cv2.aruco.DetectorParameters()
detector = cv2.aruco.ArucoDetector(aruco_dict, aruco_params)

dst_pts = np.array([
    [0, 0],
    [TARGET_W - 1, 0],
    [TARGET_W - 1, TARGET_H - 1],
    [0, TARGET_H - 1]
], dtype=np.float32)

cap = cv2.VideoCapture(0)

# Redução forçada de exposição para destacar pontos brilhantes
cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
cap.set(cv2.CAP_PROP_EXPOSURE, -6)

# Limiares HSV para Laser Vermelho
LOWER_RED1 = np.array([0, 100, 200])
UPPER_RED1 = np.array([10, 255, 255])
LOWER_RED2 = np.array([160, 100, 200])
UPPER_RED2 = np.array([180, 255, 255])

COOLDOWN_SEC = 0.25
last_shot_time = 0.0
shots = []

while True:
    ret, frame = cap.read()
    if not ret:
        break

    now = time.time()
    corners, ids, _ = detector.detectMarkers(frame)
    warped = None

    # 1. Calibração e Homografia
    if ids is not None and len(ids) >= 4:
        id_map = {ids[i][0]: corners[i][0] for i in range(len(ids))}
        if all(k in id_map for k in [0, 1, 2, 3]):
            src_pts = np.array([
                id_map[0][0],
                id_map[1][1],
                id_map[2][2],
                id_map[3][3]
            ], dtype=np.float32)
            matrix = cv2.getPerspectiveTransform(src_pts, dst_pts)
            warped = cv2.warpPerspective(frame, matrix, (TARGET_W, TARGET_H))

    # 2. Detecção no alvo retificado
    if warped is not None:
        hsv = cv2.cvtColor(warped, cv2.COLOR_BGR2HSV)
        mask = cv2.bitwise_or(
            cv2.inRange(hsv, LOWER_RED1, UPPER_RED1),
            cv2.inRange(hsv, LOWER_RED2, UPPER_RED2)
        )

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if 3 < area < 250:
                if (now - last_shot_time) > COOLDOWN_SEC:
                    M = cv2.moments(cnt)
                    if M["m00"] != 0:
                        cx = int(M["m10"] / M["m00"])
                        cy = int(M["m01"] / M["m00"])
                        dist = np.hypot(cx - CENTER_X, cy - CENTER_Y)

                        shots.append((cx, cy))
                        last_shot_time = now
                        print(f"[IMPACTO] X: {cx} | Y: {cy} | Distância do Centro: {dist:.1f}px")

        # Desenhar impactos no alvo virtual
        for sx, sy in shots:
            cv2.circle(warped, (sx, sy), 5, (0, 255, 0), -1)

        cv2.imshow("Alvo Calibrado", warped)

    cv2.imshow("Camera", frame)
    key = cv2.waitKey(1) & 0xFF
    if key == 27:
        break
    elif key in (ord('c'), ord('C')):
        shots.clear()

cap.release()
cv2.destroyAllWindows()
```

---

## Passo 3: Portabilidade para o Android APK (CameraX + OpenCV)

A transição para o ecossistema Android exige atenção ao consumo de recursos e controle direto dos sensores da câmera via CameraX.

### 3.1 Dependências Gradle (`app/build.gradle.kts`)
```kotlin
dependencies {
    // CameraX
    val cameraxVersion = "1.3.4"
    implementation("androidx.camera:camera-core:$cameraxVersion")
    implementation("androidx.camera:camera-camera2:$cameraxVersion")
    implementation("androidx.camera:camera-lifecycle:$cameraxVersion")
    implementation("androidx.camera:camera-view:$cameraxVersion")

    // OpenCV para Android SDK
    implementation(project(":opencv"))
}
```

### 3.2 Trava de Exposição e Foco (Camera2Interop)
Se a câmera mantiver o controle automático de exposição (AE), o feixe do laser causará *blooming* (estouro branco saturado no sensor). Configure:
* **AE Lock:** Travar o auto-exposure assim que o alvo for enquadrado.
* **Exposure Compensation:** Ajustar para valores mínimos negativos (ex: `-4` a `-8`), escurecendo a cena intencionalmente.
* **Focus Lock:** Bloquear autofoco para evitar oscilações durante o treino.

### 3.3 Análise de Quadros em Alta Frequência
Configure o caso de uso `ImageAnalysis` com `STRATEGY_KEEP_ONLY_LATEST` e resolução fixa ($1280 \times 720$). O processamento deve converter o buffer `ImageProxy` para `Mat` nativo do OpenCV, executar a homografia e liberar o frame (`imageProxy.close()`) imediatamente para manter 30 a 60 FPS estáveis sem gargalos no GC (*Garbage Collector*).

---

## Passo 4: Shot Timer, Lógica de Treino e Feedback Sonoro

Um sistema de *dry-fire* completo demanda cronometragem precisa para treinar tempos de saque e reação.

### 4.1 Ciclo de Temporização
1. O usuário aciona o botão **Iniciar**.
2. O sistema aguarda um intervalo pseudoaleatório entre $2{,}0\text{ s}$ e $5{,}0\text{ s}$ (para evitar antecipação de disparo).
3. Dispara o *beep* inicial de largada via `SoundPool` e marca o timestamp inicial $T_0$.
4. Ao identificar o impacto no frame de timestamp $T_{tiro}$, calcula o tempo de reação:

$$\Delta t = T_{tiro} - T_0$$

### 4.2 Métricas de Pontuação e Agrupamento
* **Alvos Circulares:** Avaliação da distância euclidiana $d = \sqrt{(x - x_0)^2 + (y - y_0)^2}$ mapeada para faixas circulares (pontos de 10 a 5).
* **Alvos de Silhueta (IPSC/IDPA):** Teste de ponto dentro de polígono com `Imgproc.pointPolygonTest()` para as zonas Alfa, Charlie e Delta.
* **Dispersão Extrema (Extreme Spread):** Distância máxima entre quaisquer dois impactos no conjunto de disparos de uma mesma série:

$$D_{max} = \max_{i, j} \|\mathbf{p}_i - \mathbf{p}_j\|$$

---

## Passo 5: Hardware, Materiais e Checklist de Setup

### 5.1 Materiais Necessários
* **Cartucho Laser de Treino (*Laser Bullet*):** Cartucho específico de câmara (ex: 9mm, .40 ou .380) cujo pino percussor aciona um pulso momentâneo de laser (duração de 50 a 100 ms).
* **Alvo Impresso (A4):** Papel com gramatura firme (120g a 180g) para evitar deformações na folha, com os 4 marcadores ArUco impressos com alto contraste nos cantos.
* **Tripé com Suporte de Celular:** Essencial para manter a câmera perfeitamente estática. Qualquer oscilação desalinha a matriz de homografia calculada.

### 5.2 Checklist de Execução
1. Fixar o alvo na parede em superfície plana, sem rugas ou curvaturas.
2. Posicionar o celular no tripé entre 1,5 m e 3 m de distância, de modo que os 4 cantos do papel estejam visíveis no visor.
3. Iniciar a calibração automática: o app deve confirmar a leitura dos 4 marcadores ArUco e travar os parâmetros de câmera.
4. Realizar o primeiro disparo de teste para verificar se o brilho da sala não está gerando falsos positivos na máscara HSV.
5. Iniciar os exercícios cronometrados.
6. Dados devem salvos em /home/adm360virtu/Arduino/TARGETS_VIRTU/backup após cada edição da última modficação.
7. Códigos que podem ser editados /home/adm360virtu/Arduino/TARGETS_VIRTU/edit/src e lidos.
8. Códigos comprimidos em  /home/adm360virtu/Arduino/TARGETS_VIRTU/data GZ prontos para ser lidos com app em completo.
9. A cada regra e edições novas devem ser inseridas em /home/adm360virtu/Arduino/TARGETS_VIRTU/Regras.
