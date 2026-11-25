# Roteiro de Desenvolvimento e Objetivos de Aprendizado

Este roteiro organiza a construção do sistema de treino com alvos de papel e laser (*Mantis Clone*) em marcos pedagógicos e práticos.

---

## Objetivo Geral do Projeto
Construir uma aplicação capaz de:
1. Identificar via câmera (webcam no computador ou câmera traseira no celular) um alvo de papel impresso em folha A4 contendo 4 marcadores ArUco nos cantos.
2. Corrigir automaticamente a perspectiva do alvo em tempo real (retificação por homografia 2D), permitindo que o celular/computador esteja em ângulos variados sem distorcer o cálculo de pontuação.
3. Detectar os disparos de laser pulsado (vermelho ou verde) emitidos por cartuchos de dry-fire (tempo de pulso ~50ms a 100ms).
4. Fornecer feedback visual e sonoro imediato: Shot Timer (tempo de reação), pontuação do impacto e histórico da série de disparos.
5. Funcionar offline no computador, rodar em domínio web sob HTTPS e ser empacotável como aplicação móvel (PWA / APK).

---

## Etapas do Roteiro (Passo a Passo)

### Etapa 1: O Alvo Físico (A4 + ArUco)
* **Conceito:** O que são marcadores fiduciais ArUco e por que são melhores que tentar detectar bordas de papel com iluminação variável.
* **Ação:**
  - Criar um gerador de alvo para impressão (PDF ou SVG pronto para imprimir em folha A4).
  - Incluir os 4 marcadores do dicionário `DICT_4X4_50` nos 4 cantos (IDs 0, 1, 2 e 3).
  - Desenhar as zonas de pontuação (círculos concêntricos de pontuação 10 a 5 e/ou silhueta estilo IPSC/IDPA).
* **Entrega:** Arquivo de alvo imprimível em A4 e script de geração.

---

### Etapa 2: A Interface Base e Captura de Câmera (PC e Celular)
* **Conceito:** Como o navegador acessa a câmera (`navigator.mediaDevices.getUserMedia`), como gerenciar resolução, FPS e controle de exposição no ambiente Web.
* **Ação:**
  - Criar a estrutura inicial em `edit/src/` (`index.html`, `style.css`, `app.js`).
  - Desenvolver o seletor de câmera (webcam do PC e câmeras traseiras do celular).
  - Implementar o loop de renderização com `<canvas>` em 60 FPS.
* **Entrega:** Página funcionando no navegador local exibindo a câmera com baixa latência.

---

### Etapa 3: Detecção dos Marcadores ArUco e Homografia (Calibração 2D)
* **Conceito:** Como o algoritmo encontra os 4 cantos na imagem e calcula a matriz de transformação de perspectiva (Homografia 2D).
* **Ação:**
  - Integrar a biblioteca de visão computacional leve para JavaScript (ou OpenCV.js / detector ArUco JS ultra-rápido).
  - Identificar os 4 marcadores e desenhar as linhas de alinhamento sobre o vídeo.
  - Extrair o alvo retificado (uma imagem perfeita de $600 \times 800$ pixels, sem ângulo ou perspectiva).
* **Entrega:** Sistema que "enxerga" o papel e exibe o alvo perfeitamente reto na tela, mesmo com a câmera inclinada.

---

### Etapa 4: Detecção Ótica do Disparo a Laser
* **Conceito:** Espaço de cor HSV, limiarização por luminosidade, detecção de contornos e momentos de centróide.
* **Ação:**
  - Isolar a cor do laser (faixa de matiz vermelho/verde e saturação/brilho alto).
  - Implementar filtro de tempo (*cooldown* de ~250ms) para não registrar múltiplos tiros no mesmo pulso de luz.
  - Mapear a coordenada $(X, Y)$ do ponto no alvo retificado.
* **Entrega:** Disparo do laser registrado com precisão e marcado com ponto visual no alvo virtual.

---

### Etapa 5: Shot Timer, Pontuação e Áudio
* **Conceito:** Cronometria de alta precisão via Web Audio API (`performance.now()`), geração de beeps sem atraso e cálculo euclidiano de pontuação.
* **Ação:**
  - Modo Treino: Beep aleatório de partida (2s a 5s).
  - Cálculo do tempo de reação: $\Delta t = T_{\text{tiro}} - T_{\text{beep}}$.
  - Cálculo dos pontos conforme anel de impacto ou polígono do alvo.
  - Relatório da série (número de disparos, agrupamento, melhor tempo, média).
* **Entrega:** Sistema completo de treino operando em tempo real.

---

### Etapa 6: Empacotamento, Otimização e Distribuição
* **Ação:**
  - Comprimir a versão de produção para `data/*.gz`.
  - Configurar Service Worker / PWA manifest para execução 100% offline.
  - Instruções de hospedagem em domínio HTTPS e opções de build para APK (Capacitor/PWA2APK).

