# Requisitos do Projeto: Sistema de Treino Dry-Fire (Estilo Mantis Laser Academy)

Este documento especifica todos os componentes, materiais físicos, dependências de software, configurações de hardware e lógica matemática necessários para construir e validar o sistema de detecção de disparos a laser.

## 1. Hardware e Materiais Físicos

| Item | Finalidade | Especificações Recomendadas | 
| ----- | ----- | ----- | 
| **Cartucho de Treino (*Laser Bullet*)** | Emissão do feixe luminoso no acionamento do percussor | Calibre correspondente (9mm, .40, .380, etc.), comprimento de onda vermelho (\~$650\text{ nm}$)  | 
| **Alvo Impresso (*Smart Target*)** | Superfície de tiro com pontos fiduciais para homografia | Folha A4/A3 rígida (papel fosco colado em papelão/MDF) com 4 marcadores ArUco nos cantos e zonas concêntricas ou silhueta no centro. | 
| **Tripé / Suporte com Garra** | Estabilidade total do sensor de imagem | Montagem rígida sem folga mecânica (qualquer vibração altera a matriz de perspectiva calculada). | 
| **Dispositivo de Captura** | Alimentação do pipeline de visão | Webcam com suporte a controle manual de exposição (protótipo) ou smartphone Android com suporte a Camera2/CameraX (app final). | 

## 2. Stack de Software e Dependências

### A. Protótipo em Computador (Python)

* **Interpretador:** Python 3.10 ou superior.

* **Bibliotecas Principais:**

  * `opencv-python` / `opencv-contrib-python` (detecção ArUco, homografia, filtragem de cores e contornos).

  * `numpy` (operações matriciais e cálculos euclidianos rápidos).

  * `matplotlib` / `scipy` (análise de agrupamento, desvio padrão e elipses de dispersão).

### B. Aplicativo Android Nativo (APK)

* **Linguagem:** Kotlin.

* **Captura de Vídeo:**

  * `androidx.camera:camera-core`

  * `androidx.camera:camera-camera2`

  * `androidx.camera:camera-lifecycle`

  * `androidx.camera:camera-view`

* **Visão Computacional:**

  * OpenCV Android SDK (módulos `core`, `imgproc` e `aruco` do contrib).

* **Persistência Local:**

  * `androidx.room:room-runtime` e `androidx.room:room-ktx` (banco de dados SQLite para sessões, histórico e pontuações).

* **Áudio de Baixa Latência:**

  * `android.media.SoundPool` (latência $< 20\text{ ms}$ para feedback sonoro de disparo e *beeps* de cronômetro).

* **Interface Gráfica:**

  * Custom Views baseadas em `Canvas` ou Jetpack Compose para plotar impactos em cima do alvo virtual.

## 3. Configurações Críticas da Câmera

Para evitar que o papel branco reflita luz e gere falsos positivos, o sensor não pode operar em modo automático:

* **Bloqueio de Exposição e Balanço de Branco:** Travar AE (*Auto Exposure*) e AWB (*Auto White Balance*).

* **Subexposição Proposital:** Configurar `exposure_compensation` no valor mínimo possível ou definir tempo de exposição manual muito baixo. O ambiente deve ficar acinzentado/escuro, fazendo com que apenas o pico de fótons do laser alcance saturação máxima.

* **Bloqueio de Foco:** Fixar o foco manual no plano do alvo para impedir variações de foco durante a sessão.

* **Taxa de Quadros (FPS):** Manter captura a $30\text{ FPS}$ estáveis ou $60\text{ FPS}$ (em $60\text{ FPS}$, um feixe de $50\text{ ms}$ dura de 3 a 4 quadros).

## 4. Pipeline Matemático e Algoritmos Necessários

```
[Frame da Câmera]
       │
       ▼
[Detecção ArUco (DICT_4X4_50)] ──► 4 Cantos (IDs 0, 1, 2, 3)
       │
       ▼
[getPerspectiveTransform + warpPerspective] ──► Imagem Retificada (W x H)
       │
       ▼
[Conversão BGR -> HSV] ──► inRange (Threshold de Vermelho/Verde)
       │
       ▼
[findContours + moments] ──► Centroide Sub-pixel (cx, cy)
       │
       ▼
[Debounce Temporal (Cooldown 200-300 ms)] ──► Tiro Confirmado?
       │
       ├──► SIM: [Cálculo de Pontuação (Euclidiana / Polígono)]
       │         [SoundPool: Tocar Som de Aço/Acerto]
       │         [Cálculo de Split / Shot Timer]
       │         [Registro no Room / Histórico]
       │
       └──► NÃO: Descartar / Próximo Quadro

```

### Regras dos Algoritmos:

1. **Calibração de Perspectiva (Homografia):**

   * Marcador `ID 0`: Canto superior esquerdo $(0, 0)$

   * Marcador `ID 1`: Canto superior direito $(W, 0)$

   * Marcador `ID 2`: Canto inferior direito $(W, H)$

   * Marcador `ID 3`: Canto inferior esquerdo $(0, H)$

2. **Faixas de Corte no Espaço HSV (Laser Vermelho):**

   * Faixa 1: $H \in [0, 10], S \in [100, 255], V \in [200, 255]$

   * Faixa 2: $H \in [160, 180], S \in [100, 255], V \in [200, 255]$

3. **Métricas de Precisão:**

   * **Distância Euclidiana ao Centro:** $d = \sqrt{(x - x_0)^2 + (y - y_0)^2}$

   * **Extreme Spread (ES):** Maior distância entre dois furos da mesma série.

   * **Mean Radius (MR):** Distância média dos tiros em relação ao baricentro $(\bar{X}, \bar{Y})$.

## 5. Permissões do Android (`AndroidManifest.xml`)

```
<uses-permission android:name="android.permission.CAMERA" />
<uses-permission android:name="android.permission.VIBRATE" />

<uses-feature android:name="android.hardware.camera" />
<uses-feature android:name="android.hardware.camera.autofocus" android:required="false" />

```