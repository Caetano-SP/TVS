# Plano de Ação Técnico: Sistema de Alvo Inteligente (Dry-Fire)

Este documento descreve exatamente o que você precisa fazer para desenvolver o seu sistema de treino a seco estilo *Mantis Laser Academy*, cobrindo desde a impressão dos marcadores e testes em computador até o desenvolvimento do aplicativo Android final.

---

## 1. Visão Geral da Arquitetura

O sistema opera em um ciclo contínuo de processamento de visão computacional em tempo real:

```
[Folha do Alvo + 4 ArUcos] 
       │
       ▼
[Câmera (Exposição Travada / Subexposta)]
       │
       ▼
[Detecção de Marcadores ArUco (Cantos 0, 1, 2, 3)]
       │
       ▼
[Homografia e Planificação (cv2.warpPerspective)]
       │
       ▼
[Filtro HSV / Detecção do Feixe Laser + Debounce]
       │
       ▼
[Mapeamento de Coordenadas (X, Y) e Pontuação]
       │
       ▼
[Feedback Sonoro / Estatísticas de Agrupamento]
```

---

## 2. O que você precisa providenciar (Materiais)

1. **Hardware:**
   * **Cartucho Laser de Treino (*Laser Bullet*):** Calibre correspondente à sua arma (ex.: 9mm, .40, .380). O pino traseiro deve ativar um pulso de 50 a 100 ms no acionamento do percussor.
   * **Tripé ou Suporte Estável para Celular:** Qualquer vibração na câmera desalinha a matriz de transformação do alvo.
   * **Webcam ou Celular como Câmera (Fase PC):** Utilizar DroidCam, Iriun ou webcam USB direta.
2. **Impressão do Alvo Físico:**
   * Folha A4 rígida (ou colada em papelão/MDF).
   * 4 marcadores do dicionário `cv2.aruco.DICT_4X4_50` impressos nos vértices exatos da área de enquadramento:
     * **ID 0:** Canto superior esquerdo $(0, 0)$
     * **ID 1:** Canto superior direito $(W, 0)$
     * **ID 2:** Canto inferior direito $(W, H)$
     * **ID 3:** Canto inferior esquerdo $(0, H)$
   * Círculos concêntricos de pontuação desenhados ou impressos no centro.

---

## 3. Roteiro Passo a Passo de Execução

### Fase 1: Validação do Algoritmo no PC e Web Edge (Python + JavaScript)
Antes de escrever código Android nativo, a matemática e o pipeline de visão computacional foram validados e operacionalizados no repositório:

* [x] **Passo 1.1 — Detecção e Homografia:**
  * Implementada detecção em Python (`test/test_homografia.py`) e em JavaScript puro 100% offline (`lib/aruco4x4.js` e `edit/src/app.js`).
  * Matriz de homografia $3 \times 3$ gerada em tempo real com retificação instantânea para o plano canônico $600 \times 800\text{ px}$.
* [x] **Passo 1.2 — Calibração dos Limiares de Cor (HSV e RGB):**
  * Subexposição e slider de sensibilidade configurável na interface web.
  * Filtro de isolamento de laser vermelho (pico nos canais vermelho e saturação) e verde, rejeitando reflexos de lâmpadas.
* [x] **Passo 1.3 — Cálculo do Centroide e Trava Temporal (*Debounce*):**
  * Extração do centroide luminoso $(X, Y)$ em cada pulso de fótons.
  * Cooldown temporal de $260\text{ ms}$ ativo para garantir 1 disparo registrado por clique do percussor.
* [x] **Passo 1.4 — Cálculo da Pontuação e Agrupamento:**
  * Teste automatizado com `scipy` em `test/test_agrupamento.py` com geração de elipse de confiança ($95\%$).
  * Cálculo em tempo real no frontend de **Extreme Spread (ES)**, **Mean Radius (MR)** e plotagem do ponto de impacto médio (**MPI**).
* [x] **Passo 1.5 — Alvo A4 Físico com ArUco e QR Code de Calibração:**
  * Gerador vetorial `edit/tools/gerar_alvo_a4.py` produzindo `edit/src/alvo_a4_imprimir.svg`.
  * 4 marcadores ArUco `DICT_4X4_50` nos cantos + QR Code de calibração inteligente contendo os metadados JSON do alvo.

---

### Fase 2: Estruturação do Projeto Android (APK)
Com o algoritmo validado no PC e na Web, esta é a próxima fase de desenvolvimento no Android Studio:

* [ ] **Passo 2.1 — Configuração do Gradle e Permissões:**
  * Adicionar permissão `android.permission.CAMERA` no `AndroidManifest.xml`.
  * Importar dependências do **CameraX** (`camera-core`, `camera-camera2`, `camera-lifecycle`, `camera-view`).
  * Integrar o **OpenCV Android SDK** (via módulo Java ou CMake/C++ NDK).
* [ ] **Passo 2.2 — Controle Manual da Câmera (Camera2Interop):**
  * Bloquear o foco automático (Focus Lock).
  * Travar o Auto Exposure (AE Lock) e o Auto White Balance (AWB Lock).
  * Forçar subexposição (`CONTROL_AE_EXPOSURE_COMPENSATION`) para que o sensor não sature com a luz ambiente.
* [ ] **Passo 2.3 — Conversão e Processamento de Quadros:**
  * Configurar o analisador do CameraX: `ImageAnalysis.Analyzer`.
  * Converter o buffer `ImageProxy` (RGBA ou YUV) para uma matriz `Mat` do OpenCV.
  * Garantir a chamada de `imageProxy.close()` ao final de cada frame para evitar congelamento do pipeline.

---

### Fase 3: Lógica de Exercícios e Interface de Usuário
* [x] **Passo 3.1 — Feedback Sonoro em Baixa Latência:**
  * Implementado via Web Audio API no protótipo web (latência $< 10\text{ ms}$); planejado via `SoundPool` no APK nativo.
* [ ] **Passo 3.2 — Shot Timer com Início Aleatório:**
  * Criar lógica de atraso aleatório (entre 2 e 5 segundos) após o toque em "Iniciar Treino".
  * Gravar o timestamp inicial $T_0$ no disparo do beep e calcular o tempo de reação no primeiro impacto:
    $$\Delta t = T_{\text{impacto}} - T_0$$
* [x] **Passo 3.3 — HUD e Persistência:**
  * HUD tático implementado exibindo os impactos numerados sobre o alvo calibrado retificado e telemetria balística.
  * Persistência de treinos e histórico (localmente via Room / SQLite no APK e API no backend).

---

## 4. Análise Técnica do Fator de Detecção: Pontos Fortes, Limitações e Reconsiderações

Esta seção documenta a análise crítica do pipeline de detecção ótica (laser e marcadores ArUco), consolidando o diagnóstico de campo e as diretrizes de aprimoramento contínuo.

### 4.1. Pontos Fortes
1. **Subtração Temporal Adaptativa de Fundo (Delta Frame / EMA):**
   * Não depende apenas de limiar estático de cor ou brilho. O algoritmo memoriza dinamicamente a iluminância do ambiente e exige uma variação transiente repentina ($\Delta R$), mitigando reflexos estáticos de lâmpadas ou brilhos pontuais na folha.
2. **Detecção Multirregime (Laser Vermelho, Zona Escura e Núcleo Saturado):**
   * Opera com 3 lógicas complementares de validação:
     * *Laser Vermelho:* dominância espectral ($R > G$ e $R > B$).
     * *Zona Escura:* sensibilidade adaptada para áreas pretas do alvo (zonas de alta pontuação do IPSC/B-27 onde o reflexo é menor).
     * *Núcleo Saturado:* detecção de ponto esbranquiçado estourado pelo excesso de lúmens em papel branco.
3. **Cálculo de Sub-pixel por Centroide Ponderado:**
   * Em vez de selecionar um pixel aleatório do feixe, calcula o centro ponderado por luminância $(X, Y)$, garantindo coordenadas estáveis mesmo diante de aberração cromática ou dispersão ótica da lente.
4. **Isolamento em Web Worker e Otimização com Transferable Objects (Zero-Copy):**
   * O processamento de imagem roda fora da thread principal de interface gráfica (evita congelamentos de UI) e utiliza reciclagem de `ArrayBuffer` por `postMessage(..., [buffer])` em modelo ping-pong, poupando o *garbage collector* e otimizando o consumo de bateria no mobile.
5. **Travamento de ArUco com Homografia 2D Pré-calculada:**
   * Uma vez travados os 4 cantos dos marcadores ArUco (dicionários estritos do OpenCV 4x4 a 7x7), a rotina de busca de marcadores é pausada e o processamento foca exclusivamente na área retificada (ROI $600 \times 800$), garantindo taxas estáveis de 30 a 60 FPS.

### 4.2. Pontos Negativos / Limitações Técnicas
1. **Dependência da Exposição Automática (AE) e Balanço de Branco da Câmera:**
   * Em smartphones com controle automático de exposição agressivo, um pulso brilhante de laser pode fazer o sensor escurecer subitamente o quadro ou desbalancear os canais RGB no frame seguinte, gerando falsos positivos na borda do pulso ou falhas de leitura.
2. **Duração do Pulso vs. Frame Rate e Rolling Shutter:**
   * Cartuchos e canetas laser de *dry-fire* emitem pulsos curtos (frequentemente de 20 ms a 50 ms). A 30 FPS (intervalo de ~33.3 ms) ou 15 FPS, se o pulso ocorrer durante a varredura de outra linha do sensor (*rolling shutter*) ou no intervalo entre exposições, o disparo pode não ser registrado.
3. **Subamostragem da Grade (Downsampling $2 \times 2$):**
   * A leitura do worker realiza salto de 2 em 2 pixels para economia de CPU. Se o ponto do laser for excessivamente diminuto ou se a distância do alvo for longa, o feixe pode cair num vão de amostragem, reduzindo a intensidade registrada.
4. **Calibração Espectral Predominantemente Voltada ao Laser Vermelho:**
   * A heurística atual baseia-se fortemente no diferencial $\Delta R$. Lasers verdes dependem de outro balanço espectral ($G > R + B$), tornando-se mais sensíveis a iluminações fluorescentes e luz solar indireta sem ajuste prévio.

### 4.3. Reconsiderações e Oportunidades de Evolução
1. **Bloqueio de Exposição e Foco via MediaTrackConstraints (Web) e Camera2Interop (APK):**
   * Solicitar via API WebRTC/`getUserMedia` o travamento de exposição e foco (`exposureMode: 'manual'`, `focusMode: 'manual'`, `whiteBalanceMode: 'manual'`) logo após o alinhamento dos ArUco, eliminando variações de ganho do sensor.
2. **Modo Específico para Laser Verde (Green Dot Detection):**
   * Implementar seletor de perfil de cor no painel de sensibilidade, permitindo alternar os filtros para priorizar o canal $G$ e diferencial $\Delta G$.
3. **Detecção em Resolução Completa com Janela Preditiva / ROI Restrita:**
   * Em vez de processar todo o alvo $600 \times 800$ com salto de 2 pixels, permitir varredura $1 \times 1$ focada nas zonas ativas de pontuação, elevando a precisão em tiros de longa distância.
4. **Filtro Temporal de Confirmação e Edge-Trigger Aprimorado:**
   * Validar se o pico luminoso surge em $T_0$ e decai rapidamente em $T_1$, diferenciando categoricamente disparos transitórios de ponteiros contínuos (que devem emitir alerta de "laser contínuo" em vez de registrar múltiplos tiros).

---

### 4.4 Teste de frametime, FPS 
1. **Atingir o baixo de frametime em qualquer opção de FPS em otimização de camêras**
* Implementar otimização em back-end no funcionamento de quais opções de camêra, desativando DNR, HDR, mas mantendo uma estabilização eletrônica. Desativar obturador 

## 5. Checklist Rápido de Verificação

| Etapa | Tarefa | Status | Observação |
| :--- | :--- | :---: | :--- |
| **Ambiente** | Instalar Python 3.10+, OpenCV e Numpy | [X] | Python 3.12, OpenCV 5.0, NumPy 2.5, SciPy 1.18 no `.venv` |
| **Alvo** | Imprimir folha A4 com marcadores ArUco (IDs 0 a 3) | [X] | Gerador SVG pronto com ArUco 4x4 e QR Code de calibração |
| **Protótipo** | Validar homografia e detecção de laser no PC | [X] | Operacional em Python e na Web a 60 FPS com retificação 2D |
| **Debounce** | Configurar trava de 250 ms por disparo registrado | [X] | Implementado com cooldown de 260 ms no motor ótico |
| **Interface** | Exibir pontuação instantânea, agrupamento e sons de tiro | [X] | Estatísticas ao vivo (ES, MR, MPI, Split) e Web Audio API |
| **Exposição** | Travar AE/AWB e escurecer a imagem na câmera do celular | [~] | Em andamento (slider de sensibilidade no Web; trava total no APK) |
| **Android SDK** | Criar projeto Android com suporte a CameraX e OpenCV | [ ] | **Próxima fase** (Fase 2 do roteiro para empacotamento em APK) |

## [2026-11-23] - Plano de Endurance Locust (1 Hora com 1000 usuários e otimização do back-end)
*Plano formal detalhado documentado em:* [Regras/08_plano_endurance_locust_1000_usuarios.md](file:///home/adm360virtu/Arduino/TARGETS_VIRTU/Regras/08_plano_endurance_locust_1000_usuarios.md)

Tarefas para hoje:
* [x] **Etapa 1:** Camada de Cache Híbrida L1 (RAM Zero-Copy) + L2 (Redis com Fallback Transparente).
* [x] **Etapa 2:** Índices Compostos no Banco, Filas Assíncronas de Alta Vazão e Sharding sob Demanda.
* [x] **Etapa 3:** Motor Concorrente para 1.000+ Conexões, API Stateless e Load Balancer / Auto-Scaling.
* [x] **Etapa 4:** Atualização do Locustfile para Rampa de 1.000 Usuários (`-u 1000 -r 100`) e Endurance de 1 Hora (`3600s`).
* [x] **Etapa 5:** Validação Automatizada de Carga Zero Erros, Gráfico de Escalabilidade e Registro em `Regras/`.

---

## [2026-11-24] - Plano de Execução: Mitigação de Latência de Cauda (P95) e Saturação (500 a 1.000 Usuários)
*Plano formal detalhado documentado em:* [Regras/09_plano_mitigacao_p95_e_saturacao_1000_usuarios.md](file:///home/adm360virtu/Arduino/TARGETS_VIRTU/Regras/09_plano_mitigacao_p95_e_saturacao_1000_usuarios.md)

Etapas executadas (5/5 concluídas e homologadas):
* [x] **Etapa 1:** Otimização do Backlog TCP (`4096`), Pre-Warming do `DynamicWorkerPool` e Tuning de Socket/TLS.
* [x] **Etapa 2:** Governança Adaptativa de `HTTP/1.1 Keep-Alive` e Reciclagem de Conexões sob Alta Concorrência (500–1.000 usuários).
* [x] **Etapa 3:** Fast-Path Lock-Free (Micro-TTL) para `GET [OCULTO]` e Cache de Cabeçalho HTTP `Date` (1 Hz).
* [x] **Etapa 4:** Calibração do Teste Escalatório Locust e Validação Estrita de SLA (`>= 1.000 req/s`, `P95 < 1.000 ms`, `0,00%` erros).
* [x] **Etapa 5:** Homologação da Suíte Completa, Regeneração dos Artefatos em `test/output/` e Registro em `Regras/`.

---

## [2026-11-24] - Plano de Estresse e Saturação Empírica: 10.000 Usuários Simultâneos (Zero Modificação no Back-end)
*Plano formal detalhado documentado em:* [Regras/10_plano_estresse_10000_usuarios_zero_modificacao.md](file:///home/adm360virtu/Arduino/TARGETS_VIRTU/Regras/10_plano_estresse_10000_usuarios_zero_modificacao.md)

Tarefas do teste de estresse empírico:
* [x] **Etapa 1:** Definição da Matriz de Carga Escalatória (1.000 → 2.500 → 5.000 → 10.000 Usuários a 250/s) e Roteiro de Descoberta.
* [x] **Etapa 2:** Criação do Harness de Teste Isolado (`test/test_locust_10000_users.py`) com 6 Processos Locust em 12 Núcleos.
* [x] **Etapa 3:** Documentação Formal em `Regras/10_plano_estresse_10000_usuarios_zero_modificacao.md`.
* [x] **Etapa 4:** Execução do Teste de Estresse de 10.000 Usuários e Coleta de Métricas em `test/output/locust_10000_*`.
* [x] **Etapa 5:** Diagnóstico e Análise Técnica Comparativa do Comportamento Observado.
