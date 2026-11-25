# Plano de Diagnóstico, Testes e Melhoria do Front-end (TARGETS_VIRTU)

* **Data de Criação:** 2026-11-25
* **Escopo:** Auditoria técnica, plano de testes automatizados (`test/test_frontend_diagnostics.py`) e roteiro de refatoração cirúrgica do Front-end (`edit/src/index.html`, `edit/src/style.css`, `edit/src/app.js`, `edit/src/ballistics_echarts.js` e Web Workers).
* **Restrição Operacional:** Nenhuma modificação nos arquivos de `edit/src/` deve ser executada sem autorização explícita do usuário. Toda futura implementação deve obedecer rigorosamente às diretrizes de `Regras/00_diretrizes_e_fluxo.md`, `Regras/05_style_css.md`, `Regras/06_regras_de_visual.md` e `Regras/07_performance_bigo_e_caching.md`.

---

## 1. Diagnóstico do Front-end Atual (Pontos Fracos, Gargalos e Onde Ocorrem Erros)

A auditoria estática e comportamental dos arquivos de `edit/src/` revelou **8 pontos críticos** onde ocorrem falhas silenciosas, desalinhamentos geométricos, desperdício de memória ou divergência em relação às regras do projeto:

### 1.1. Desconexão entre o Front-end e a API do Back-end (`[OCULTO]` e `[OCULTO]`)
* **Localização:** `edit/src/app.js` (linhas 298–329) e `edit/src/ballistics_echarts.js` (linhas 803–860).
* **Causa:** Embora o back-end (`edit/tools/servidor_dev.py`) possua persistência assíncrona SQLite WAL Sharded homologada para alta concorrência (`POST [OCULTO]` retornando `202 Accepted` em $\mathcal{O}(1)$, `GET [OCULTO]` e `GET [OCULTO]`), **não há nenhuma chamada `fetch()` no front-end**.
* **Impacto:** As funções `saveSession()` e `persistRounds()` apenas limpam o armazenamento local (`clearVolatileStorage()`). Nenhum disparo ou rodada real efetuada pelo atirador no navegador ou smartphone é enviado ao banco SQLite do servidor.

### 1.2. Quebra do Fluxo Zero-GC ("Ping-Pong Buffer") entre `app.js` e os Web Workers
* **Localização:** `edit/src/app.js` (linhas 183–212, 456–469 e 1141–1154).
* **Causa:** Os workers (`aruco-worker.js` e `laser-worker.js`) devolvem `payload.recycledBuffer` via lista `Transferable`, e o `app.js` armazena a referência em `state.recycledArucoBuffer` e `state.recycledLaserBuffer`, mas **nunca reutiliza esses buffers** na captura seguinte. A cada frame, `cameraCtx.getImageData(...)` e `roiCtx.getImageData(...)` alocam um novo `ImageData` / `ArrayBuffer` e descartam o buffer reciclado.
* **Impacto:** Pressão contínua sobre o *Garbage Collector* (GC churn) em 15–60 FPS, contrariando a diretriz Zero-GC de `Regras/07_performance_bigo_e_caching.md`.

### 1.3. Aba 3 (`Estatísticas`), Shot Timer e Reconhecimento de Zonas Bloqueados + Layout Desktop Quebrado
* **Localização:** `edit/src/index.html` (linhas 33, 138–190, 205, 423) e `edit/src/style.css` (linhas 1001–1038).
* **Causa:**
  1. `Regras/05_style_css.md` especifica **4 Abas Dedicadas** (`tab-camera`, `tab-alvo`, `tab-estatisticas`, `tab-ajustes`) no mobile e layout em **2 colunas (`390px 1fr`)** com navegação segmentada no `.site-header` no Desktop (`>= 1080px`).
  2. Atualmente, a classe `.is-hidden-module { display: none !important; }` oculta permanentemente a Aba 3 (`#panel-stats`), o Shot Timer (`#btn-start-round`), os KPIs da rodada (`.round-kpi-grid`) e a barra de perfil/zonas (`.target-toolbar-row`), apesar de `echarts.min.js` (~1 MB) e `ballistics_echarts.js` serem carregados no startup.
  3. No Desktop (`@media (min-width: 1080px)` em `edit/src/style.css`, linhas 1023–1033), a regra `.tab-card { display: block; }` força todas as abas não ocultas a serem exibidas simultaneamente empilhadas (`Câmera + Alvo` na coluna esquerda e `Ajustes` na direita), inutilizando os botões `.nav-desktop-btn` do cabeçalho.

### 1.4. Desalinhamento de Coordenadas da ROI do Laser com Câmera Espelhada (`isFlipped`) ou Zoom Digital
* **Localização:** `edit/src/app.js` (linhas 1077–1105 e 1874–1883).
* **Causa:**
  1. Se o usuário alterna a chave **"Espelhar Câmera Horizontalmente"** (`#chk-flip`) após o alvo já estar travado (`state.isTargetLocked === true`), a matriz de homografia `state.homographyMatrix` e os cantos `state.targetCorners` permanecem nas coordenadas antigas (não espelhadas), projetando impactos invertidos no eixo $X$.
  2. Em `extractLaserRoiImageData()`, quando o alvo está travado e o usuário não está na aba Câmera, o recorte da ROI é extraído diretamente do elemento `<video>` com conversão proporcional `srcX`/`srcY`, enquanto a homografia foi calculada sobre `cameraCanvas` (`cw x ch`). Divergências de *aspect ratio* ou arredondamento de `startX`/`startY` introduzem desvio espacial no impacto registrado.

### 1.5. Falhas Silenciosas de Permissão de Câmera, Troca de Resolução e Perda de Stream
* **Localização:** `edit/src/app.js` (linhas 1386–1418 e 1623–1660).
* **Causa:**
  1. **Seletor de lente antes da autorização:** `listCameras()` é chamado antes de `getUserMedia()`, quando o navegador oculta os rótulos (`device.label`) e IDs das lentes por privacidade.
  2. **Erros de câmera invisíveis na interface:** Quando `getUserMedia()` falha (permissão negada, câmera bloqueada por outro processo ou acesso via HTTP sem TLS), a mensagem de erro é gravada apenas em `#diagnostics-panel`, que possui a classe `.sr-only` (oculta visualmente). O usuário clica em "ATIVAR CÂMERA" e não recebe feedback visual explicando a causa do bloqueio.
  3. **Troca de resolução com alvo travado:** Em `applyCameraResolution()`, alterar a resolução (`640x480`, `960x540`, `1280x720`) reinicia o stream e altera `cameraCanvas.width`/`height`, **mas não chama `unlockTarget()`**, mantendo uma matriz de homografia calculada para outra dimensão de pixels.

### 1.6. Botão de Calibração Manual (4 Pontos) Ausente no DOM e Descarte Prematuro de Rodadas na Sessão
* **Localização:** `edit/src/app.js` (linhas 49, 1819–1847 e 1865–1872) e `edit/src/ballistics_echarts.js` (linhas 831–860).
* **Causa:**
  1. O código de Calibração Manual por 4 pontos está implementado em `app.js` (`btnManualCalibrate`, listener `pointerdown` em `cameraCanvas`), porém **o elemento `#btn-manual-calibrate` não existe em `edit/src/index.html`** (`null`). Caso reflexos ou sombras impeçam a leitura automática de 1 dos 4 marcadores ArUco, o usuário não consegue acionar o fallback manual.
  2. Em `archiveAndPrepareNextRound()` (`ballistics_echarts.js`, linhas 848–850), clicar em **"NOVA RODADA"** limpa `analyticsState.previousRound = null` e `analyticsState.roundsHistory = []`, impedindo que a tabela de Histórico de Rodadas e os gráficos comparativos (*Radar Rodada Atual vs. Anterior* e *Evolução por Rodada*) funcionem durante a sessão ativa em RAM.

### 1.7. Retificação de Perspectiva (`warpTargetView`) Lenta na Main Thread e Sem Interpolação Bilinear
* **Localização:** `edit/src/app.js` (linhas 769–791).
* **Causa:** O duplo laço de $600 \times 800 = 480.000$ pixels chama `Homography.projectPoint(H_inv, { x: tx, y: ty })` a cada iteração, alocando **960.000 objetos efêmeros** `{ x, y }` na thread principal no instante do Auto-Lock e usando amostragem *nearest-neighbor* (`Math.round`), o que causa travamento momentâneo (~40–90 ms) e serrilhado visual no alvo retificado.

### 1.8. Código Morto e Complexidade $\mathcal{O}(S^2)$ Duplicada em `app.js`
* **Localização:** `edit/src/app.js` (linhas 18–20, 50–53, 918–1050).
* **Causa:** `updateBallisticMetrics()` em `app.js` recalcula Extreme Spread e Mean Radius em $\mathcal{O}(S^2)$ a cada disparo, duplicando o cálculo incremental $\mathcal{O}(S)$ já existente em `TargetAnalytics.computeRoundSummary()` (`ballistics_echarts.js`). Há também seletores órfãos (`#btn-beep-test`, `#btn-manual-calibrate`) e botões mortos em `.sr-only` (`#tab-pip`, `#tab-target`, `#tab-camera`, `#btn-flip`, `#btn-toggle-camera`).

---

## 2. Plano de Testes Automatizados do Front-end (`test/test_frontend_diagnostics.py`)

Seguindo as diretrizes de `Regras/02_funcao_test.md`, a primeira etapa prática consiste em criar uma suíte de diagnóstico automatizada em `test/test_frontend_diagnostics.py` (executada via `unittest` + Node.js) para aferir e validar o front-end em 5 frentes:

1. **Auditoria Automatizada das 15 Regras Proibitivas (`Regras/05_style_css.md` e `Regras/06_regras_de_visual.md`):**
   - Verificação estática de `edit/src/index.html`, `edit/src/style.css`, `edit/src/app.js` e `edit/src/ballistics_echarts.js` garantindo:
     - Zero gradientes (`linear-gradient`, `radial-gradient`, `conic-gradient`);
     - Zero cantos arredondados acima de `4px` (`border-radius <= 4px`, sem pílulas `999px` ou `50%` em botões/cards);
     - Zero tipografia `Inter`;
     - Zero `backdrop-filter` (vidro fosco);
     - Zero efeitos de `glow`, `text-shadow` ou `@keyframes pulse`;
     - Zero `font-weight` 800 ou 900;
     - Ícones SVG com `stroke-width >= 2`;
     - Zero emojis em todo o código-fonte de interface.
2. **Auditoria de Integridade DOM ↔ JavaScript:**
   - Mapeamento cruzado de todos os `getElementById` / `querySelector` de `app.js` e `ballistics_echarts.js` contra o DOM real de `index.html`, apontando IDs inexistentes (`#btn-manual-calibrate`, `#btn-beep-test`), painéis de erro presos em `.sr-only` e módulos bloqueados por `.is-hidden-module`.
3. **Teste de Invariantes Geométricas (Resolução, Flip, Zoom e Homografia):**
   - Simulação via Node.js do fluxo `lockTargetWithCorners` seguido de mudanças de resolução (`640x480` → `1280x720`), alternância de `isFlipped` e projeção de coordenadas da ROI para o plano canônico $600 \times 800$, validando erro sub-pixel ($< 1,5\text{ px}$).
4. **Benchmark de Frametime e Alocação de Memória (`warpTargetView` e Workers):**
   - Medição do tempo de execução de `warpTargetView(true)` ($600 \times 800$) e verificação do ciclo de vida de `previousRound` e `roundsHistory` ao acionar `archiveAndPrepareNextRound()`.
5. **Teste de Integração Front-end ↔ Back-end (`POST [OCULTO]` e `GET [OCULTO]`):**
   - Validação do contrato JSON entre o resumo de rodada gerado por `TargetAnalytics.computeRoundSummary()` e o endpoint `POST [OCULTO]` de `edit/tools/servidor_dev.py`.

### 2.1. Resultado da Execução da Etapa 1 (2026-11-25)

* **Comando:** `.venv/bin/python3 -m unittest discover -s test -p test_frontend_diagnostics.py -v`
* **Resultado:** `41` testes | `25` aprovados (guardas de regressão) | `16` *expected failures* (defeitos confirmados) | `0` erros.
* **Relatório:** `test/output/frontend_diagnostics_report.json`.
* **Convenção:** cada defeito conhecido é um teste `@unittest.expectedFailure` vinculado à sua Entrega. Quando a correção for aplicada, o teste acusará *unexpected success* (suíte vermelha), sinalizando que o decorador deve ser removido e o teste promovido a guarda de regressão.

| Frente | Resultado medido | Veredito |
| :--- | :--- | :--- |
| 1. Regras Proibitivas | 0 gradientes, 0 `border-radius > 4px`, 0 Inter, 0 `backdrop-filter`, 0 glow/pulse, 0 `font-weight` 800/900, 6 SVGs com `stroke-width >= 2`, 0 emojis | Conforme |
| 2. DOM ↔ JS | Órfãos: `#btn-manual-calibrate`, `#btn-beep-test`; 7 elementos em `.is-hidden-module`; 5 botões mortos em `.sr-only`; grid Desktop `1.05fr 1fr` (não `390px 1fr`); `.tab-card` forçado a `display:block` no Desktop; sem `#camera-error-banner` | Confirmado (1.3, 1.5.2, 1.6.1, 1.8) |
| 3. Geometria | Homografia obsoleta após troca de resolução: **729 px** de erro; após espelhamento: **480 px**; após zoom 2x: **438 px**. Re-lock com cantos novos: **0,0 px**. `applyCameraResolution()` e `#chk-flip` não destravam o alvo. `listCameras()` também é chamado no startup (linha 2003). Zoom já bloqueado com alvo travado. | Confirmado (1.4.1, 1.5.1, 1.5.3) |
| 3. ROI do Laser | 24 cenários (zoom 1x–3x, flip, 640x480/1280x720 e canvas com aspecto diferente do vídeo): erro **0,0 px** entre `extractLaserRoiImageData()` e `drawCameraView()` | **Refutado** (1.4.2) — mapeamento consistente |
| 4. Performance | `warpTargetView(true)`: **480.000** chamadas a `projectPoint` (~960.000 objetos), mediana **~181 ms** (Node.js); referência inline Zero-GC equivalente bit a bit: **~9,6 ms** (ganho estimado **~95%**). Buffers reciclados: 0 leituras. `updateBallisticMetrics()` com 3 laços `for` (O(S²)). | Confirmado (1.2, 1.7, 1.8) — custo maior que o estimado |
| 4. Rodadas | `stopCurrentRound()` arquiva 1 snapshot; `archiveAndPrepareNextRound()` zera o histórico (1 → 0); **`previousRound` nunca recebe valor em nenhum ponto do código** | Confirmado (1.6.2) + **novo achado** |
| 5. Contrato Back-end | Payload derivado de `computeRoundSummary()` (611 bytes) aceito com `202` e recuperado íntegro via `GET [OCULTO]` (`target`, `shot_count`, `points`, `hitFactor`, `time`, `shots`, `zoneCounts`); 0 `fetch('[OCULTO]')` no front-end | Contrato válido; integração ausente (1.1) |

> **Ajustes ao diagnóstico:** (a) o item 1.4.2 não se confirmou — a Entrega 2, ação 2, pode ser reduzida a um teste de regressão; (b) o radar comparativo da Entrega 3 exige também **atribuir `previousRound`** em `stopCurrentRound()`/`archiveAndPrepareNextRound()`, não apenas deixar de limpá-lo.

---

## 3. Plano de Melhorias do Front-end (Execução Condicionada à Aprovação)

Após a aprovação do usuário, as correções serão aplicadas em **5 entregas incrementais**:

### Entrega 1 — Blindagem de Estado da Câmera, Resolução, Flip, Fallback Manual e Feedback Visual de Erros
* **Arquivos:** `edit/src/index.html`, `edit/src/style.css`, `edit/src/app.js`.
* **Ações:**
  1. Adicionar um banner técnico visível de diagnóstico/erro dentro do Card de Permissão (`#camera-error-banner`) para informar claramente caso a permissão da câmera seja negada, o dispositivo não possua câmera ou o contexto HTTPS seja inválido.
  2. Em `applyCameraResolution()` e no toggle de espelhamento (`#chk-flip`), caso `state.isTargetLocked` esteja ativo, executar `unlockTarget()` automaticamente (ou recalcular a matriz espelhada) para impedir disparos com homografia desalinhada.
  3. Restaurar o botão **`CALIBRAR MANUAL (4 PONTOS)`** (`#btn-manual-calibrate`) na Aba 1 (`Câmera`) como contingência operacional para ambientes com iluminação crítica.

### Entrega 2 — Otimização Zero-GC no `warpTargetView()` e Unificação Métrica $\mathcal{O}(S)$
* **Arquivos:** `edit/src/app.js`, `edit/src/workers/aruco-worker.js`, `edit/src/workers/laser-worker.js`.
* **Ações:**
  1. Reescrever o loop $600 \times 800$ de `warpTargetView()` aplicando a projeção homográfica inversa inline (`h00..h22`) sem instanciar objetos `{ x, y }` e com amostragem bilinear rápida, eliminando as 960.000 alocações por lock e reduzindo a latência em ~75%.
  2. Garantir alinhamento geométrico exato entre `extractLaserRoiImageData()` e o `cameraCanvas` mesmo sob zoom digital ou imagem espelhada.
  3. Substituir o loop $\mathcal{O}(S^2)$ de `updateBallisticMetrics()` em `app.js` pela leitura direta em $\mathcal{O}(1)$ do resumo memoizado de `TargetAnalytics.computeRoundSummary()`.

### Entrega 3 — Reativação Limpa das 4 Abas (`Regras/05_style_css.md`), Shot Timer e Histórico Volátil em Sessão
* **Arquivos:** `edit/src/index.html`, `edit/src/style.css`, `edit/src/ballistics_echarts.js`.
* **Ações:**
  1. Remover o bloqueio `.is-hidden-module` da **Aba 3 (`Estatísticas`)**, do **Shot Timer (`INICIAR RODADA`)**, dos **KPIs da rodada** e do seletor de **Perfil/Zonas**, mantendo a interface limpa e 100% aderente às 15 regras de `Regras/06_regras_de_visual.md`.
  2. Ajustar `@media (min-width: 1080px)` em `edit/src/style.css` para cumprir `Regras/05_style_css.md` (layout Desktop em 2 colunas `390px 1fr` com navegação funcional no `.site-header`).
  3. Em `archiveAndPrepareNextRound()` (`ballistics_echarts.js`), manter `previousRound` e `roundsHistory` em memória RAM durante a sessão aberta (limpando apenas no `resetAllRounds()` ou ao fechar/recarregar a página), permitindo o uso real dos gráficos *Radar Comparativo* e *Evolução por Rodada*.

### Entrega 4 — Integração Assíncrona Não-Bloqueante com o Back-end (`POST [OCULTO]`)
* **Arquivos:** `edit/src/ballistics_echarts.js`, `edit/src/app.js`.
* **Ações:**
  1. Ao encerrar uma rodada com disparos válidos (`stopCurrentRound()` / `archiveAndPrepareNextRound()`), enviar de forma assíncrona e não-bloqueante (`fetch('[OCULTO]', { method: 'POST', keepalive: true })`) o payload da série (`session_id`, `target`, `shots`, `points`, `hitFactor`, `time`) para o back-end SQLite WAL.
  2. Implementar *silent fallback* (`catch (_) {}`) para que o modo 100% offline no APK Android continue operando sem erros quando não houver servidor HTTP local.

### Entrega 5 — Sincronização, Validação Completa e Registro em `Regras/`
* **Arquivos:** `backup/src/`, `data/`, `android_app/www/`, `Regras/historico_alteracoes.md`.
* **Ações:**
  1. Executar `.venv/bin/python3 -m unittest discover -s test -v` garantindo 100% de aprovação em todos os testes de back-end e front-end.
  2. Executar `.venv/bin/python3 edit/tools/gerenciador_projeto.py all` para sincronizar `backup/src/`, gerar pacotes `.gz` e `.br` em `data/` e atualizar `android_app/www/`.
  3. Registrar todas as modificações em `Regras/historico_alteracoes.md`.

---

## 4. Checklist de Acompanhamento

| Etapa | Descrição | Status |
| :---: | :--- | :---: |
| **0** | Diagnóstico técnico do Front-end e documentação do plano em `Regras/11_plano_diagnostico_e_melhoria_frontend.md` | `[x]` |
| **1** | Criação e execução da suíte de testes de diagnóstico (`test/test_frontend_diagnostics.py`) | `[x]` |
| **2** | Entrega 1: Blindagem de estado de câmera, resolução, flip, fallback manual 4 pontos e banner de erro | `[ ]` |
| **3** | Entrega 2: Otimização Zero-GC no `warpTargetView()`, alinhamento ROI e unificação métrica $\mathcal{O}(S)$ | `[ ]` |
| **4** | Entrega 3: Reativação das 4 Abas (`Regras/05_style_css.md`), Shot Timer e histórico de rodadas em RAM | `[ ]` |
| **5** | Entrega 4: Integração assíncrona não-bloqueante com `POST [OCULTO]` no Back-end | `[ ]` |
| **6** | Entrega 5: Sincronização (`gerenciador_projeto.py all`), bateria de testes e log em `historico_alteracoes.md` | `[ ]` |
