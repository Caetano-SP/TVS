# Histórico de Alterações do Projeto

Este arquivo registra todas as modificações, criações de módulos e decisões técnicas realizadas no repositório `TARGETS_VIRTU`.

---

## [2026-11-04] - Inicialização da Estrutura e Diretrizes do Projeto

* **Responsável:** Equipe de Desenvolvimento 
* **Objetivo:** Organizar a arquitetura de pastas, estabelecer regras de fluxo de trabalho (edit, backup, data, Regras) e definir o roteiro de aprendizado passo a passo.
* **Modificações Realizadas:**
  1. Criação do diretório de contingência `backup/src/`.
  2. Criação do diretório de utilitários `edit/tools/`.
  3. Criação de `Regras/00_diretrizes_e_fluxo.md` formalizando a política de edição apenas em `edit/src`, preservação em `backup/src`, compressão em `data/` e documentação obrigatória em `Regras/`.
  4. Criação de `Regras/01_roteiro_e_objetivos.md` definindo as 6 etapas estruturadas de desenvolvimento (Alvo ArUco, Captura Web/Câmera, Retificação 2D, Detecção Laser, Shot Timer e Empacotamento PWA/APK/Domínio).
  5. Criação da ferramenta de automação `edit/tools/gerenciador_projeto.py` para sincronizar backup e comprimir arquivos em `.gz` para `data/`.
* **Como Validar:**
  - Verificar existência e integridade dos arquivos em `Regras/`, `edit/` e `backup/`.
  - Executar `python3 edit/tools/gerenciador_projeto.py status` para conferir o status do ambiente.

---

## [2026-11-04] - Definição do Backend, Acesso à Câmera e QR Code para Alvos e Projeções

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Estabelecer a arquitetura do backend, os requisitos de segurança e hardware para câmera (celular e PC) e o sistema de identificação por QR Code.
* **Modificações Realizadas:**
  1. Criação de `Regras/02_funcao_test.md` (criada pelo usuário) definindo o papel dos testes automatizados na pasta `test/`.
  2. Atualização de `Regras/00_diretrizes_e_fluxo.md` integrando o diretório `test/` e a nova documentação.
  3. Criação de `Regras/04_arquitetura_backend_e_camera.md` detalhando:
     - Por que a visão computacional roda na borda/cliente (para registrar pulsos de 50ms sem lag de rede);
     - As responsabilidades do Backend (HTTPS local para desbloquear câmera no smartphone, servir arquivos `.gz`, API de alvos e persistência de treinos);
     - Configuração de câmera no celular (lente traseira, resolução, controle de exposição);
     - Mecanismo de leitura de QR Code para carregar metadados do alvo impresso ou calibrar o tamanho da projeção na parede.
* **Como Validar:**
  - Conferir a documentação detalhada em `Regras/04_arquitetura_backend_e_camera.md`.

---

## [2026-11-04] - Integração do Repositório Mantis-Target-Analysis em lib/

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Integrar o repositório de engenharia reversa dos alvos Mantis (`lib/Mantis-Target-Analysis-main`) para reaproveitamento dos modelos de alvos, extração de ArUcos e validação de testes.
* **Modificações Realizadas:**
  1. Criação do arquivo de regras `Regras/regras_de_teste_e_integra_o_do_reposit_rio.md` definindo ambiente virtual (.venv), regras de teste e critérios de sucesso.
  2. Atualização de `Regras/03_funcao_lib.md` formalizando a finalidade da pasta `lib/` para abrigar dependências e submódulos.
  3. Garantia de modularidade em Python com criação de `lib/Mantis-Target-Analysis-main/__init__.py`.
  4. Criação do link simbólico `lib/mantis_target_analysis -> Mantis-Target-Analysis-main` para compatibilidade total com os scripts e regras do projeto.
* **Como Validar:**
  - Executar `python3 edit/tools/gerenciador_projeto.py status` para conferir a detecção dos componentes.

---

## [2026-11-04] - Criação do Ambiente Virtual e Primeiro Teste de Homografia

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Configurar o `.venv` com dependências científicas de visão computacional e validar a detecção de ArUco e Homografia 2D com imagens de alvos reais do Mantis.
* **Modificações Realizadas:**
  1. Criação do ambiente virtual `.venv` e instalação de `opencv-python`, `numpy` e `matplotlib`.
  2. Implementação do teste automatizado `test/test_homografia.py`:
     - Leitura dos alvos oficiais do Mantis (`lib/Mantis-Target-Analysis-main/images/`).
     - Detecção dos marcadores ArUco (`DICT_4X4_50`).
     - Descoberta: o alvo de controle usa os IDs `[0, 1, 2, 3]` e o alvo IPSC usa os IDs `[24, 25, 26, 27]`.
     - Cálculo da matriz de homografia e retificação do alvo para $600 \times 800$ pixels salva em `test/output/ipsc_retificado_600x800.png`.
* **Como Validar:**
  - Executar: `.venv/bin/python3 test/test_homografia.py` (deve passar com `OK` em menos de 0.1s).

---

## [2026-11-04] - Criação da Interface Web e Servidor Seguro Local

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Desenvolver a primeira versão interativa da interface Web em `edit/src/` com suporte a captura de câmera (PC e celular), renderização a 60 FPS e servidor HTTPS local.
* **Modificações Realizadas:**
  1. Criação de `edit/src/index.html`: layout responsivo mobile-first, HUD tático para enquadramento do alvo e painel de controle.
  2. Criação de `edit/src/style.css`: tema tático escuro de alto contraste, elementos visuais para marcação e HUD de calibração.
  3. Criação de `edit/src/app.js`:
     - Seletor de câmeras (suporte a câmeras traseira/frontal e webcam);
     - Loop de animação via `requestAnimationFrame` em 60 FPS calculando taxa real;
     - Sintetizador de áudio Web Audio API (beep de largada sem latência);
     - Hook de processamento para injeção da visão computacional (laser e ArUco).
  4. Criação de `edit/tools/servidor_dev.py`: servidor Python leve com suporte a HTTPS e geração de certificados para permitir acesso imediato à câmera do celular pela rede Wi-Fi.
  5. Sincronização automática para `backup/src/` e geração dos pacotes comprimidos em `data/*.gz`.
* **Como Validar:**
  - Iniciar o servidor com `python3 edit/tools/servidor_dev.py` e acessar pelo navegador no PC ou celular.

---

## [2026-11-04] - Implementação do Detector de Laser e Marcação no Alvo Calibrado

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Adicionar a detecção ótica em tempo real de pulsos de laser (vermelho/verde), plotagem no Alvo Calibrado virtual com furos numerados, telemetria de split de tiro e adequação estética estrita às regras visuais de `Regras/05_style_css.md`.
* **Modificações Realizadas:**
  1. Adequação estética rigorosa segundo `Regras/05_style_css.md`:
     - Remoção de todos os emojis (substituídos por texto técnico limpo);
     - Eliminação de gradientes, cantos arredondados excessivos (máximo 4px mantido), efeitos glow, sombras excessivas e tipografia Inter;
     - Adoção de design tático, sóbrio e flat de alto contraste.
  2. Implementação do **Alvo Calibrado Virtual** (`#calibrated-canvas`, 600x800):
     - Desenho em tempo real da silhueta IPSC com zonas A (Alfa), C (Charlie) e D (Delta) e marcadores ArUco de referência;
     - Cada impacto é desenhado como um furo de bala numerado (`1`, `2`, `3`...) com anel de alta visibilidade;
     - Painel com Total de Tiros, Última Coordenada $(X, Y)$ e Tempo Split;
     - Botão "Zerar Alvo" para reiniciar a série de treinos.
  3. Implementação do **Detector Ótico de Laser** em `edit/src/app.js`:
     - Amostragem em alta velocidade mantendo 60 FPS;
     - Filtro seletivo de cor (Laser Vermelho ou Verde) com limiar de brilho ajustável por slider;
     - Debounce temporal de 280ms para registrar exatamente 1 tiro por acionamento do percussor;
     - Mapeamento das coordenadas da câmera para o plano 600x800 do alvo calibrado;
     - Disparo imediato do beep sonoro de confirmação via Web Audio API.
  4. Suporte a 3 modos de visualização:
     - "Visão Dupla" (câmera + alvo lado a lado);
     - "Câmera" (tela cheia da câmera);
     - "Alvo Calibrado" (tela cheia do alvo com os furos).
  5. Sincronização automática para `backup/src/` e geração dos arquivos compactados em `data/*.gz`.
* **Como Validar:**
  - Iniciar `python3 edit/tools/servidor_dev.py`, ligar a câmera, apontar um laser (ou lanterna/objeto vermelho de teste) e verificar o registro numerado no alvo calibrado com o beep sonoro.

---

## [2026-11-04] - Reconhecimento de Alvos ArUco e Retificação por Homografia 2D em Tempo Real

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Implementar o reconhecimento automático dos 4 marcadores de canto dos alvos oficiais do Mantis, resolver a distorção de perspectiva (câmera inclinada, alvos em pé ou deitados, projeção em parede) e retificar a imagem para o plano 600x800 do alvo calibrado.
* **Modificações Realizadas:**
  1. Criação do módulo `lib/aruco4x4.js` (JavaScript puro, 100% offline):
     - Conversão em escala de cinza e limiarização adaptativa por Imagem Integral $O(1)$;
     - Rastreamento de contornos e aproximação de polígonos quadriláteros convexos;
     - Decodificação de bits do dicionário oficial `DICT_4X4_50` nas 4 orientações (0°, 90°, 180°, 270°);
     - Motor matemático de Homografia 2D com resolução de sistema linear $8 \times 8$ via eliminação gaussiana e inversão de matrizes $3 \times 3$.
  2. Integração do Reconhecimento e Homografia em `edit/src/app.js`:
     - Rastreamento em tempo real dos marcadores ArUco com desenho do polígono verde nos 4 cantos sobre o vídeo;
     - Identificação automática do modelo de alvo (IPSC IDs 24, 25, 26, 27 / Controle IDs 0, 1, 2, 3);
     - Retificação de perspectiva: o alvo na parede/papel (mesmo inclinado ou projetado) é recortado e achatado para a proporção canônica $600 \times 800$;
     - Mapeamento matemático exato dos disparos a laser: $X_{\text{alvo}} = \mathbf{H} \cdot X_{\text{câmera}}$, garantindo precisão balística independente do ângulo ou distância da câmera.
  3. Atualização do servidor `edit/tools/servidor_dev.py`:
     - Suporte a servir bibliotecas de `lib/` e imagens de alvos oficiais de `lib/Mantis-Target-Analysis-main/images/`.
     - Novo seletor no frontend permitindo testar diretamente com os alvos oficiais (IPSC, IDPA, Controle, B-27) ou carregar fotos do dispositivo além da câmera ao vivo.
  4. Sincronização automática para `backup/src/` e compressão `.gz` em `data/`.
* **Como Validar:**
  - Iniciar `python3 edit/tools/servidor_dev.py` e abrir a página: o sistema já carrega o alvo IPSC oficial com os 4 marcadores detectados e a imagem retificada no painel direito. Pode alternar para a câmera ou selecionar outros alvos.

---

## [2026-11-04] - Instalação de Dependências Faltantes, Métricas Balísticas e Gerador de Alvo A4

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Suprir dependências faltantes identificadas no diagnóstico de `Regras/especifica_o_de_requisitos_e_depend_ncias.md`, unificar `requirements.txt`, implementar testes e cálculo em tempo real de agrupamento (Extreme Spread, Mean Radius, MPI) e gerar o alvo imprimível oficial A4 com marcadores ArUco.
* **Modificações Realizadas:**
  1. **Ambiente e Dependências Unificadas:**
     - Confirmação e validação do `scipy` (`v1.18.1`) no ambiente virtual `.venv`.
     - Criação do arquivo unificado raiz `requirements.txt` com as bibliotecas do projeto (`numpy`, `opencv-python`, `matplotlib`, `scipy`).
  2. **Testes Automatizados de Agrupamento (`test/test_agrupamento.py`):**
     - Implementação do teste de cálculo de dispersão balística com `scipy.spatial.distance.pdist` e `numpy`.
     - Validação matemática de Extreme Spread (ES), Mean Radius (MR) e Ponto Médio de Impacto (MPI).
     - Geração gráfica da série de tiros com elipse de dispersão (intervalo de confiança 95% via `scipy.stats.chi2`) salva em `test/output/agrupamento_serie.png`.
  3. **Gerador de Alvo Imprimível A4 (`edit/tools/gerar_alvo_a4.py`):**
     - Script gerador do alvo oficial A4 em SVG vetorial de alta precisão (`edit/src/alvo_a4_imprimir.svg`).
     - Integra os 4 marcadores ArUco (`DICT_4X4_50`, IDs 0, 1, 2, 3), anéis de pontuação concêntricos (5 a 10/X) e régua milimétrica de calibração física (50 mm).
  4. **Integração no Frontend (`edit/src/index.html`, `app.js` e `style.css`):**
     - Adicionados indicadores em tempo real de `AGRUPAMENTO (ES)` e `RAIO MEDIO (MR)` na barra de estatísticas.
     - Plotagem visual em tempo real do centroide MPI (mira tática verde) e linha de dispersão máxima no alvo virtual retificado.
     - Inclusão do botão "Imprimir Alvo A4" e opção de carregar o alvo A4 gerado no seletor de entrada.
  5. **Sincronização de Contingência e Produção:**
     - Sincronização automática para `backup/src/` e geração dos pacotes comprimidos em `data/*.gz` via `edit/tools/gerenciador_projeto.py`.
* **Como Validar:**
  - Executar os testes automatizados: `.venv/bin/python3 test/test_agrupamento.py` e `.venv/bin/python3 test/test_homografia.py`.
  - Iniciar o servidor dev (`edit/tools/servidor_dev.py`), acessar o frontend e verificar as métricas de ES e MR se atualizando a cada disparo (ou ao clicar no alvo), bem como abrir o link do alvo A4 imprimível.

---

## [2026-11-04] - Integração do ArUco com QR Code de Calibração e Mapeamento de Tarefas

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Esclarecer e alinhar o momento atual do projeto frente ao `Regras/roteiro_de_desenvolvimento_e_tarefas.md`, integrar a calibração de escala física por QR Code conjuntamente com a homografia ArUco no alvo oficial e no frontend.
* **Modificações Realizadas:**
  1. **Atualização do Alvo A4 (`edit/tools/gerar_alvo_a4.py` -> `edit/src/alvo_a4_imprimir.svg`):**
     - Embutido QR Code com metadados JSON do alvo (ID, dimensões em mm, ArUcos esperados e escala $0.35\text{ mm/px}$) posicionado junto aos 4 marcadores ArUco (`DICT_4X4_50`).
  2. **Detecção e Calibração por QR Code no Frontend (`edit/src/app.js`):**
     - Adicionado scanner de QR Code em paralelo ao rastreamento ArUco via `window.BarcodeDetector`.
     - Leitura e atualização automática do modelo do alvo, dimensões e escala física no cabeçalho e telemetria.
  3. **Mapeamento de Status no Roteiro de Tarefas (`Regras/roteiro_de_desenvolvimento_e_tarefas.md`):**
     - Atualização do checklist de verificação apontando a conclusão de toda a **Fase 1** (Protótipo PC/Web, homografia, laser, debounce, agrupamento balístico) e posicionando a **Fase 2** (módulo nativo Android CameraX) como próximo passo.
  4. **Sincronização:**
     - Sincronização para `backup/src/` e geração dos arquivos comprimidos `.gz` em `data/`.
* **Como Validar:**
  - Abrir o SVG gerado `edit/src/alvo_a4_imprimir.svg` e conferir a presença dos 4 ArUcos nos cantos e o QR Code no rodapé.
  - No frontend (`edit/tools/servidor_dev.py`), selecionar a opção "Alvo A4 Gerado" ou apontar a câmera: o sistema calibra a homografia pelos 4 marcadores e lê o QR Code carregando os metadados do alvo.

---

## [2026-11-04] - Refatoração Completa: Câmera Real 100%, Auto-Lock ArUco, Suporte ao B-27 e Modo PiP

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário via /grill-me)
* **Objetivo:** Eliminar o mock de seleção de alvos estáticos e focar 100% no fluxo da câmera ao vivo; corrigir o bug de detecção que impedia calibrar o B-27 e outros alvos densos; implementar Auto-Lock inteligente com trava de estabilidade e transição dinâmica para Picture-in-Picture (PiP).
* **Modificações Realizadas:**
  1. **Correção do Motor ArUco (`lib/aruco4x4.js`):**
     - Corrigida a amostragem de perspectiva da matriz de bits de cada marcador ($H_{\text{grid} \to \text{quad}}$).
     - Ajustado o algoritmo de Douglas-Peucker para contornos fechados, eliminando a rejeição do 5º vértice de fechamento.
     - Ajustado o passo horizontal de varredura ($x{++}$) para capturar todas as transições de borda preto/branco.
     - Adicionado método `detectMarkersDownsampled` operando em resolução adaptativa (480px) com reescalonamento matemático, alcançando 30-60 FPS mesmo em smartphones.
     - Validada a detecção imediata dos 4 cantos de todos os alvos oficiais Mantis (B-27 [IDs 8, 9, 10, 11], IPSC [IDs 24, 25, 26, 27], Controle [IDs 0, 1, 2, 3]).
  2. **Auto-Lock Inteligente e Transição PiP (`edit/src/app.js` e `edit/src/style.css`):**
     - Rastreamento contínuo da folha com contador de estabilidade: ao manter o alvo firme por 850ms, o sistema trava a retificação (Auto-Lock) e emite sinal sonoro duplo.
     - Transição dinâmica de layout: no Auto-Lock, o alvo retificado assume a tela cheia e o fluxo da câmera comuta para uma janela flutuante PiP no canto superior direito.
     - Enquanto travado, o processamento de ArUco é pausado, dedicando 100% da CPU à detecção do laser a 60 FPS ininterruptos.
     - Botão *"Recalibrar Alvo"* sempre disponível para destravar e refazer o auto-lock.
  3. **Calibração Manual de Contingência (Fallback 4 Pontos):**
     - Adicionado botão *"Calibrar Manual (4 Pontos)"*: permite ao usuário tocar nos 4 cantos da folha na tela para calcular a homografia mesmo sob iluminação desfavorável.
  4. **Eliminação dos Mocks de Imagem Estática:**
     - Removido o seletor `source-select` do layout e código; o sistema inicializa ativando a câmera física diretamente.
  5. **Sincronização e Produção:**
     - Executado `gerenciador_projeto.py all`, sincronizando para `backup/src/` e comprimindo para `data/*.gz`.
* **Como Validar:**
  - Iniciar `python3 edit/tools/servidor_dev.py` e acessar pelo navegador: a câmera liga diretamente;
  - Ao apontar para qualquer folha de papel na parede (B-27, IPSC ou folha com marcadores), o assistente exibe a barra de estabilidade e trava o alvo no centro em escala real com a câmera em miniatura PiP;
  - Disparar pulsos de laser (ou clicar na tela para simular) e conferir os furos numerados, split e métricas balísticas.

---

## [2026-11-05] - Compilação do APK Standalone Android (Targets Virtu)

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Superar o bloqueio de rede Wi-Fi/firewall local no celular empacotando toda a aplicação e motor de visão computacional diretamente em um arquivo APK instalável para smartphone Android.
* **Modificações Realizadas:**
  1. **Estruturação do Ambiente Nativo (`android_app/`):**
     - Inicialização do projeto Capacitor integrado ao SDK Android local (`/home/adm360virtu/Android/Sdk`, plataformas 34/35).
     - Configuração de permissões de câmera nativa no `AndroidManifest.xml` (`CAMERA`, `hardware.camera` e `hardware.camera.autofocus`).
     - Sincronização de todos os assets de `edit/src/` e `lib/aruco4x4.js` para o pacote de produção `assets/public/`.
  2. **Compilação com Sucesso via Gradle:**
     - Build executado com sucesso em 21s gerando `targets_virtu.apk` (4.0 MB) em `edit/tools/apk/targets_virtu.apk`.
     - 100% offline: dispensa Wi-Fi, certificados SSL ou servidores locais; executa a visão computacional diretamente no hardware do dispositivo.
* **Como Validar:**
  - Transferir o arquivo `edit/tools/apk/targets_virtu.apk` para o smartphone via cabo USB, Google Drive, WhatsApp ou adb (`adb install edit/tools/apk/targets_virtu.apk`).
  - Abrir o app no celular: a câmera traseira inicializa diretamente com FPS nativo sem dependência de rede.

---

## [2026-11-05] - Otimização de Sensibilidade do Laser, Eficiência de Bateria e UI Mobile

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Resolver a não-detecção do laser no smartphone decorrente de filtros de limiar excessivamente restritivos, eliminar o desperdício de bateria por processamento desnecessário de quadro inteiro e reformular a interface mobile para alta usabilidade e enquadramento tático.
* **Modificações Realizadas:**
  1. **Sensibilidade do Laser Vermelho Calibrada para Sensores Mobile (`edit/src/app.js`):**
     - O sensor de câmera do celular comprime a gama dinâmica: o ponto de laser de 50ms raramente atinge $R \ge 232$ em alta velocidade.
     - Ajustado o limiar padrão de brilho para `210` e pureza cromática $R - G \ge 38$ (com tolerância $1 \le \text{pixels} \le 120$).
     - Removido o filtro de fundo estático que bloqueava pulsos rápidos de cartucho laser de treino.
  2. **Eficiência Energética e Bateria (Redução de 50%+ de consumo de CPU):**
     - **Região de Interesse (ROI Dinâmica):** após a identificação ou auto-lock do alvo, o sistema passa a processar apenas a área do papel (+ margem de 30px) em vez de varrer todos os 1280x720 pixels da imagem.
     - **Controle de Cadência de Processamento:** a análise de imagem do laser opera a um ritmo otimizado de ~35 FPS (`laserIntervalMs: 28`), liberando os núcleos de processamento do smartphone e impedindo aquecimento e drenagem rápida de bateria.
  3. **Interface Mobile Responsiva e Tática (`edit/src/style.css` e `index.html`):**
     - Adequação completa para telas de smartphones na vertical (`max-width: 768px`):
     - Barra de botões reformatada em grade 3x2 tática, eliminando rolagem e elementos quebrados.
     - Modo PiP ajustado para proporções perfeitas na tela móvel (alvo calibrado ocupando 100% da visualização e miniatura PiP compacta no canto superior).
     - Tipografia, espaçamentos e estatísticas milimétricas compactadas conforme `Regras/05_style_css.md`.
  4. **Atualização do APK Standalone:**
     - Sincronizado para `android_app/` e recompilado via Gradle com sucesso gerando o novo `edit/tools/apk/targets_virtu.apk`.
* **Como Validar:**
  - Instalar o APK atualizado no smartphone;
  - Verificar que o layout se ajusta perfeitamente à tela vertical sem transbordar;
  - Disparar o laser vermelho no alvo A4: o aplicativo registra imediatamente o impacto com sinal sonoro sem aquecer o smartphone.

---

## [2026-11-11] - Implementação do Motor ArUco Universal (3x3 a 7x7) e Multi-Dicionário

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Expandir a leitura de marcadores fiduciais para além do 4x4 (Mantis), permitindo ler ArUcos menores (3x3) e maiores (5x5, 6x6 e 7x7) de forma automática e integrada ao Web Worker e Homografia 2D.
* **Modificações Realizadas:**
  1. **Criação do Catálogo de Dicionários (`lib/aruco_dictionaries.js`):**
     - Mapeamento binário e representações compactas BigInt das famílias `DICT_3X3_50`, `DICT_4X4_50`, `DICT_5X5_50`, `DICT_6X6_50` e `DICT_7X7_50`.
  2. **Motor Universal Paramétrico (`lib/aruco_universal.js`):**
     - Parametrização da malha $N \times N$ (totalCells = $N + 2$).
     - Amostragem com 5 subpontos por célula contra ruído e reflexos.
     - Detecção em cascata multi-dicionário: prioriza 4x4 e faz fallback automático para 3x3, 5x5, 6x6 e 7x7.
  3. **Desacoplamento em Web Worker (`edit/src/workers/aruco-worker.js` e `edit/src/app.js`):**
     - O processamento de imagem roda fora da thread principal da interface.
     - O Auto-Lock agora identifica a família detectada no título do alvo (ex: `ALVO 3X3_50`, `ALVO 5X5_50`).
  4. **Testes Automatizados (`test/test_aruco_universal.py`):**
     - Testes unitários cobrindo integridade dos dicionários e execução do motor universal.
  5. **Sincronização e Atualização do APK:**
     - Sincronizado para `backup/src/` e `.gz` em `data/`.
     - Compilação do APK via Gradle finalizada com sucesso em `edit/tools/apk/targets_virtu.apk`.
* **Como Validar:**
  - Instalar o APK atualizado no celular (`adb install edit/tools/apk/targets_virtu.apk` ou via cópia direta);
  - Apontar a câmera para qualquer alvo com 4 ArUcos (seja 3x3, 4x4, 5x5, 6x6 ou 7x7): o aplicativo detecta a malha, realiza a homografia 2D e trava o alvo normalmente.

---

## [2026-11-11] - Otimização de Câmera a 30 FPS (Eco) e 60 FPS (Turbo) com Deduplicação e Backpressure

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Implementar operação otimizada em 30 FPS e 60 FPS com seletor em tempo real, eliminando reprocessamento de quadros duplicados, fila de Web Workers e desperdício de GPU/CPU.
* **Modificações Realizadas:**
  1. **Seletor de Taxa de Quadros (`edit/src/index.html` e `edit/src/app.js`):**
     - Adicionado seletor `60 FPS (Turbo)` / `30 FPS (Eco)` na barra de controle com troca em tempo real via `track.applyConstraints({ frameRate })`.
  2. **Deduplicação de Quadros de Vídeo (`video.currentTime`):**
     - O loop principal (`renderLoop`) só executa `drawImage`, `getImageData` e análise de laser/ArUco quando o hardware da câmera efetivamente disponibiliza um quadro novo, zerando ciclos ociosos.
  3. **Controle de Backpressure nos Web Workers (`arucoWorkerBusy` / `laserWorkerBusy`):**
     - Evita leitura da GPU (`getImageData`) caso o Worker ainda esteja processando o frame anterior, mantendo latência mínima e fluidez constante.
  4. **Sincronização e APK:**
     - Backup atualizado em `backup/src/`, pacotes comprimidos em `data/*.gz` e APK recompilado em `edit/tools/apk/targets_virtu.apk`.
* **Como Validar:**
  - Alternar entre `60 FPS (Turbo)` e `30 FPS (Eco)` na interface e observar o indicador de FPS real na barra superior, além da resposta imediata aos disparos de laser sem aquecimento do aparelho.

---

## [2026-11-11] - Redesenho Estrutural por Barras (Desktop e Mobile) conforme `Regras/06_regras_de_visual`

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Eliminar informações espalhadas e organizar toda a interface em barras horizontais estruturadas e simplificadas, cumprindo rigorosamente as 15 regras proibitivas de `Regras/06_regras_de_visual`.
* **Modificações Realizadas:**
  1. **Adequação Estrita às 15 Regras Proibitivas (`edit/src/style.css` e `edit/src/app.js`):**
     - Removidos todos os gradientes (`linear-gradient` do skeleton), animações de pulso (`@keyframes pulse`), sombras (`box-shadow` no PiP) e efeitos de glow nas animações de tiro.
     - Bordas retas táticas (`border-radius: 2px`), linhas de enquadramento reforçadas ($\ge 2\text{px}$) e pesos tipográficos controlados ($\le 700$).
  2. **Arquitetura em 4 Barras Horizontais (`edit/src/index.html` e `edit/src/style.css`):**
     - **Barra 1 (Topo):** Logo, seletor de modo (`PiP`, `Alvo`, `Camera`) e badges de status em linha única.
     - **Barra 2 (Fita de Telemetria Balística Unificada):** Promovida para o topo logo abaixo do cabeçalho com células divisórias fixas (`DISPAROS`, `COORDENADA`, `SPLIT`, `AGRUP. (ES)`, `RAIO (MR)` e botão `ZERAR`), permanecendo visível em qualquer modo de câmera ou alvo.
     - **Barra 3 (Gaveta de Ajustes e Utilitários):** Agrupa `Lente`, `Taxa (30/60 FPS)`, `Brilho`, `Filtro R-G` e utilitários (`ESPELHAR`, `BEEP`, `A4 SVG`, `JSON`, `CSV`). No smartphone, inicia recolhida automaticamente para dedicar máxima tela ao alvo, abrindo com 1 toque no botão `Ajustes`.
     - **Barra 4 (Comando Principal + Fita de Diagnóstico):** Grade de 4 botões primários (`Ligar Camera`, `Recalibrar`, `Manual (4 Pts)`, `Ajustes`) e linha de status monoespaçada.
  3. **Sincronização e APK:**
     - Sincronizado em `backup/src/`, comprimido em `data/*.gz` e APK atualizado em `edit/tools/apk/targets_virtu.apk`.
* **Como Validar:**
  - Abrir no Desktop ou instalar o APK no Smartphone: todas as informações estão alinhadas em barras horizontais fixas sem sobreposição, rolagem ou elementos soltos.






---

## [2026-11-05] - Exclusividade Laser Vermelho (650nm), Filtro Anti-Lâmpada e Robustez ArUco 4x4

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Eliminar o laser verde para focar exclusivamente no laser vermelho (650nm), resolver falsos disparos por saturação de lâmpadas/reflexos da sala e aprimorar a estabilidade de captura dos 4 marcadores ArUco em folhas A4 estáticas.
* **Modificações Realizadas:**
  1. **Foco Exclusivo em Laser Vermelho e Filtro Anti-Lâmpada (`edit/src/index.html` e `edit/src/app.js`):**
     - Removido o seletor de laser verde da interface e do código.
     - Adicionados 3 controles dedicados de ajuste fino:
       - **Limiar Brilho (`180` a `255`, padrão `232`):** intensidade mínima do canal vermelho (`R`).
       - **Filtro Luz R-G (`25` a `150`, padrão `65`):** pureza cromática exigindo $(R - G) \ge \text{pureza}$, $(R - B) \ge \text{pureza}$ e teto de saturação branca ($G < 225, B < 215$) para barrar lâmpadas brancas/amarelas.
       - **Escurecer Sala (`0%` a `75%`, padrão `20%`):** aplica redução de exposição na lente (`exposureCompensation`) e atenuação óptica no quadro quando o alvo está travado.
     - **Mapa de Iluminância Estática de Fundo (`bgLuminanceMap`):** o detector memoriza continuamente pontos de luz constantes (lâmpadas ou reflexos fixos na folha) e exige salto transiente repentino ($\Delta R \ge 24$) para validar apenas pulsos reais de laser.
  2. **Estabilidade de Captura dos 4 ArUcos (`lib/aruco4x4.js` e `edit/src/app.js`):**
     - **Multi-Pass Adaptive Threshold:** varredura dupla automática (`window=21, cutoff=7` e `window=13, cutoff=5`) caso menos de 4 marcadores sejam vistos na primeira passagem.
     - **Subamostragem Ampliada (`640px`):** maior definição para marcadores ArUco pequenos em folha A4 a distância.
     - **Amostragem de 5 Subpontos por Célula + Limiar Dinâmico Min-Max:** evita falha de leitura quando reflexo de lâmpada atinge a borda preta do marcador.
     - **Memória Temporal de Marcadores (`markerCache`, TTL 380ms):** retém marcadores entre frames adjacentes caso o autofoco da câmera oscile momentaneamente.
  3. **Sincronização e Produção:**
     - Executado `gerenciador_projeto.py all`, atualizando `backup/src/` e `data/*.gz`.
* **Como Validar:**
  - Iniciar `python3 edit/tools/servidor_dev.py` e apontar para o alvo A4 sob luz ambiente: os 4 ArUcos travam de forma rápida e sem perda intermitente.
  - Ajustar "Filtro Luz (R-G)" e "Escurecer Sala" caso haja lâmpadas fortes diretas, verificando que lâmpadas acesas não disparam tiros falsos.

---

## [2026-11-11] - Novo Visual em 4 Abas Dedicadas (Estilo `MASTER_IPSC` + `Regras/06_regras_de_visual`) e Card de Permissão de Câmera

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Eliminar o excesso de informações simultâneas na tela, adotar o padrão visual e de navegação do projeto `MASTER_IPSC` (adaptado às 15 regras proibitivas de `Regras/06_regras_de_visual`) e exigir permissão explícita antes de iniciar a captação da câmera.
* **Modificações Realizadas:**
  1. **Paleta e Identidade Visual `MASTER_IPSC` (`edit/src/style.css`):**
     - Tema Claro Padrão (`#f0f2f5` fundo, `#ffffff` cards, `#4caf50` verde primário, `#e53935` perigo) com suporte a Modo Escuro (`#161c24` / `#212b36`) persistido na sessão.
     - Cumprimento estrito das 15 regras de `Regras/06_regras_de_visual`: sem fonte Inter, sem gradientes, sem `backdrop-filter`, cantos $\le 4\text{px}$, sem sombras excessivas, sem glow/pulso e sem pesos tipográficos $800/900$.
  2. **Navegação por 4 Abas Dedicadas (`edit/src/index.html`, `edit/src/style.css`, `edit/src/app.js`):**
     - **Aba 1 (`Câmera`):** Card Central de Permissão de Câmera com seletor de lente e botão `AUTORIZAR E INICIAR CÂMERA` (a câmera não liga sozinha ao abrir). Após autorizada, exibe o visor óptico com guia ArUco e o botão `RECALIBRAR ALVO`. Ao travar os 4 marcadores, muda automaticamente para a Aba 2.
     - **Aba 2 (`Alvo`):** Exibe exclusivamente o plano retificado do alvo ocupando a área útil e o botão `ZERAR`, sem métricas sobrepostas.
     - **Aba 3 (`Estatísticas`):** Painel dedicado com Total de Disparos, Split Time, Coordenada, Extreme Spread, Mean Radius, tabela de Histórico de Disparos (`#`, `COORDENADA`, `SPLIT`) e botão `ZERAR SÉRIE` (sem botões de exportação).
     - **Aba 4 (`Ajustes`):** Área exclusiva com Taxa de Quadros (`60 FPS` / `30 FPS`), chaves `ESPELHAR IMAGEM` e `MODO ESCURO (DARK)`, e sliders `Sensibilidade de Brilho` e `Filtro Anti-Reflexo (R-G)`.
  3. **Layout Híbrido Responsivo:**
     - **Mobile (`<= 1079px`):** Exibe estritamente 1 aba por vez com barra inferior `.bottom-nav`.
     - **Desktop (`>= 1080px`):** Exibe layout de 2 colunas (`390px 1fr`) com menu segmentado no `.site-header` e grade dedicada para a aba `Ajustes`.
  4. **Sincronização e APK:**
     - Sincronizado em `backup/src/`, comprimido em `data/*.gz` e recompilado no APK Android (`edit/tools/apk/targets_virtu.apk`).
* **Como Validar:**
  - Abrir o site ou instalar o APK: verificar que a câmera inicia desligada exibindo o Card de Permissão na Aba 1.
  - Clicar em `AUTORIZAR E INICIAR CÂMERA`, apontar para os 4 marcadores ArUco e confirmar a transição automática para a Aba 2 (`Alvo`).
  - Navegar entre as 4 abas verificando a separação limpa entre Câmera, Alvo, Estatísticas e Ajustes.

### 2026-11-11 — Elevação Visual com Apache ECharts, Reconhecimento de Área do Alvo e Rodadas (Shot Timer + Hit Factor)
* **O que foi feito:**
  1. **Apache ECharts Offline (`edit/src/lib/echarts.min.js`, v6.1.0):**
     - Biblioteca oficial `echarts.min.js` empacotada localmente para funcionamento 100% offline no navegador e no APK Android.
     - Implementados 5 gráficos interativos no painel `Estatísticas` em `edit/src/ballistics_echarts.js`:
       - **Mapa do Alvo, Zonas e MPI (`#chart-target-map`):** Visualiza área reconhecida, anéis/polígonos do alvo, impactos numerados por zona e Ponto Médio de Impacto (MPI).
       - **Medidor Hit Factor (`#chart-hf-gauge`):** Gauge semicircular em tempo real.
       - **Distribuição por Zona (`#chart-zones-bar`):** Barras dinâmicas (`X/10..M` para A4/B-27 ou `A/C/D/M` para IPSC/IDPA).
       - **Radar Comparativo (`#chart-radar-compare`):** Compara `Rodada Atual vs Rodada Anterior` em 5 eixos (Precisão, Hit Factor, Pontos, Velocidade e Agrupamento).
       - **Evolução por Rodada (`#chart-rounds-evolution`):** Série temporal combinada de `Hit Factor` e `Tempo (s)`.
  2. **Reconhecimento de Área e Zonas do Alvo:**
     - Criado `analyzeTargetImageData` / `scanTargetArea` para detectar contraste, bounding box útil, dimensões em milímetros e centro do alvo na imagem retificada `600x800`, somando aos modelos ArUco (`VIRTU_A4`, `IPSC`, `IDPA`, `B27`), com sobreposição vetorial no canvas e seletor de perfil na Aba `Alvo`.
  3. **Gestão de Rodadas & Shot Timer (Padrão MASTER_IPSC):**
     - Adicionado painel de rodada com **Cronômetro**, **Hit Factor**, **Pontos**, **Última Zona**, botões **`INICIAR RODADA`**, **`NOVA RODADA`**, **`LIMPAR`**, sinal sonoro Web Audio, persistência em `localStorage` e tabela de histórico de rodadas.
  4. **Conformidade Visual (`Regras/06_regras_de_visual` e `Regras/05_style_css.md`):**
     - Mantido tema plano Claro/Escuro sem gradientes, sem `backdrop-filter`, sem brilho/glow, com `border-radius <= 4px`, sem fonte Inter, sem pesos `800/900` e sem emojis.
* **Como Validar:**
  - Abrir a Aba `Alvo`, selecionar ou detectar o alvo (`AUTO`, `VIRTU_A4`, `IPSC`, `IDPA`), iniciar uma rodada (`INICIAR RODADA`), disparar no alvo com o laser, clicar em `NOVA RODADA` e verificar os 5 gráficos Apache ECharts na Aba `Estatísticas`.

### 2026-11-11 — Correção de Câmera Pós-Lock ArUco, Detecção Adaptativa de Laser e Remoção de Disparo por Clique
* **Causas Identificadas e Corrigidas:**
  1. **Congelamento da câmera após travar os 4 ArUco e falha ao reativar em Recalibrar:**
     - `edit/src/style.css` ocultava `#panel-camera` (e o `<video>`) com `display: none` ao mudar para a Aba `Alvo`, levando o Chromium/Android WebView a pausar o decodificador de vídeo e congelar os quadros. Mantido `#panel-camera:not(.active-tab)` e `<video>` renderizados dentro da viewport com `2x2px` e `opacity: 0.01`, garantindo fluxo contínuo de vídeo em todas as abas.
     - Removida transferência destrutiva do `ArrayBuffer` em `aruco-worker.js` / `app.js`, adicionado guarda de exceção e retomada automática de `video.play()` em `renderLoop`, e garantido em `unlockTarget()` (Recalibrar) o reinício seguro da câmera e do cache quando o stream/loop estiver pausado ou encerrado.
     - `warpTargetView()` passa a preservar o snapshot retificado no lock sem re-varrer pixels desnecessariamente a cada disparo.
  2. **Disparos a laser não detectados independentemente da sensibilidade:**
     - Substituído o filtro estático em `edit/src/workers/laser-worker.js` e no fallback de `edit/src/app.js` por **subtração temporal de fundo (Delta Frame EMA)** combinada com 3 regimes de detecção (`isRedLaser`, `isDarkZoneLaser` em áreas pretas e `isSaturatedCore` para centro estourado no papel branco), além de centroide ponderado e reset automático do buffer de fundo no lock/unlock.
  3. **Disparo acidental ao clicar/tocar na imagem do alvo ou da câmera:**
     - Removidos os ouvintes de disparo simulado por `pointerdown` em `#calibrated-canvas` e em `#camera-canvas` (que agora aceita cliques apenas quando `state.manualCalibrationMode` estiver ativo).
* **Como Validar:**
  - Ativar a câmera na Aba 1, travar os 4 ArUco (transição automática para `Alvo`), tocar no alvo confirmando que nenhum disparo falso é registrado, disparar com laser sobre fundo branco e escuro confirmando detecção e clicar em `TROCAR LENTE / RECALIBRAR` para confirmar o retorno fluido da câmera.

### 2026-11-13 — Paridade ArUco 4x4..7x7 (A4..A2), UI Enxuta, Controle de FPS (10/15/30/60) e Memória 100% Volátil
* **O que foi feito:**
  1. **Detecção ArUco Estrita em Paridade com OpenCV (`edit/tools/aruco_dictionaries_all.json`, `edit/src/lib/aruco_dictionaries.js`, `lib/aruco_dictionaries.js`, `edit/src/lib/aruco_universal.js`, `lib/aruco_universal.js`):**
     - Gerados os dicionários canônicos completos do OpenCV (`DICT_4X4_250`, `DICT_5X5_250`, `DICT_6X6_250`, `DICT_7X7_250`, 250 IDs cada) e removido `DICT_3X3_50` para eliminar falsos positivos em texto e formas comuns.
     - Reescrito `aruco_universal.js` com busca `Map` $O(1)$, exigência de `bitErrors === 0`, borda externa 100% preta, validação de *quiet zone* branca externa, correção da ordem horária de cantos nas rotações de 90°/270°, filtragem rigorosa de quadriláteros e método unificado `ArUcoUniversalDetector.identifyTargetCorners(markers, frameWidth, frameHeight)` exigindo 4 IDs distintos do mesmo dicionário, escala consistente e polígono convexo para folhas Retrato de A4 até A2.
  2. **Simplificação Visual e Ocultação de Módulos Avançados (`edit/src/index.html`, `edit/src/style.css`, `edit/src/ballistics_echarts.js`, `edit/src/app.js`):**
     - Ocultados via classe `.is-hidden-module` o painel `#panel-stats` (Hit Factor e gráficos ECharts), seletores de modelo/perfil/zona (`.target-toolbar-row`, `#target-recognition-info`), `.round-kpi-grid`, `#btn-start-round` e botões de navegação para `Estatísticas`, mantendo os scripts carregados para reativação futura e pulando a inicialização/renderização do ECharts enquanto oculto.
     - Removida a chamada `drawGroupingOverlay` do fluxo ativo em `app.js` e configurado `showZonesOverlay: false` em `ballistics_echarts.js`, mantendo apenas a imagem limpa do alvo retificado (`600x800`), plotagem de impactos `(X, Y)` e ações `NOVA RODADA` / `LIMPAR`.
  3. **Telemetria de FPS, Opções de Taxa de Quadros e Redução de Aquecimento (`edit/src/index.html`, `edit/src/style.css`, `edit/src/app.js`, `edit/src/workers/aruco-worker.js`, `edit/src/workers/laser-worker.js`):**
     - Exposto o contador de FPS ao vivo no cabeçalho (`#badge-fps`) e na faixa da câmera (`#camera-fps-val` e `#camera-aruco-state`).
     - Adicionadas as opções `10 FPS` e `15 FPS` ao seletor `#fps-select` com padrão `30 FPS` (além de `60 FPS`), aplicando throttling real em `renderLoop`.
     - Pausada a detecção de laser enquanto o alvo não está travado (`!state.isTargetLocked`), limitada a busca ArUco a no máximo `10 FPS` (`>= 100ms`) com parada após lock, e adotada cópia zero via `Transferable` (`[buf]`) nos dois Web Workers.
  4. **Sessão 100% Volátil em RAM (`edit/src/app.js`, `edit/src/ballistics_echarts.js`):**
     - Removida a persistência em `localStorage` e o autosave periódico; criada `clearVolatileStorage()` (executada na inicialização e em `pagehide`/`beforeunload`) e ajustada `archiveAndPrepareNextRound()` para descartar impactos e histórico a cada rodada.
  5. **Testes e Sincronização:**
     - Atualizado `test/test_aruco_universal.py` para validar paridade 4x4..7x7 com `cv2.aruco` em perspectivas A4/A3/A2, ausência de `3X3`, IDs > 50 (`136..139`) e zero falsos positivos em cenas sem ArUco.
* **Como Validar:**
  - Executar `.venv/bin/python3 -m unittest discover -s test -v` (9 testes passando).
  - Abrir a aplicação via `edit/tools/servidor_dev.py`, confirmar `30 FPS` padrão no seletor, opções `10/15/30/60 FPS`, contador de FPS visível no topo e na câmera, UI enxuta sem Hit Factor/ECharts/tracejos de zona e descarte completo dos dados ao trocar de rodada ou fechar a página.

### 2026-11-13 (Etapa 2) — Backend Concorrente Gzip, Pipeline Android/Build, Desbloqueio de FPS no Celular e Cadências Rápidas (150ms–220ms)
* **O que foi feito (Fases 1, 3 e 4 do Plano + Ajustes de Campo no Samsung A32):**
  1. **Fase 1 — Servidor HTTPS Concorrente, SAN e Gzip (`edit/tools/servidor_dev.py`):**
     - Substituído `HTTPServer` por `ThreadedHTTPSServer` (`ThreadingHTTPServer` com `daemon_threads = True`, `allow_reuse_address = True` e timeout de 12s por socket) para suportar múltiplas requisições simultâneas do celular sem travamentos.
     - Atualizado `gerar_certificado_autoassinado()` para incluir `Subject Alternative Name` (`IP` local, `127.0.0.1`, `localhost`) e regenerar automaticamente quando o IP local mudar (`.cert/ip.txt`), além de atualizar `edit/src/qrcode_conexao.png`.
     - Implementado suporte a `Content-Encoding: gzip` a partir de `data/*.gz` (`--gzip` ou quando atualizado e cliente enviar `Accept-Encoding: gzip`) e validação anti path-traversal via `is_relative_to`.
  2. **Fase 3 — Pipeline de Build e Limpeza (`edit/tools/gerenciador_projeto.py`):**
     - Adicionada `sincronizar_android()` (comando `android`, integrado ao `all`) espelhando `edit/src/` -> `android_app/www/` com limpeza de arquivos obsoletos (`_espelhar_diretorio`).
     - Removidos arquivos legados não utilizados (`edit/src/lib/aruco4x4.js` e `edit/tools/aruco_dictionaries.json`), reduzindo o pacote comprimido em `data/`.
  3. **Fase 4 + Desbloqueio de FPS Mobile & Cadência Rápida (`edit/src/index.html`, `edit/src/app.js`, `edit/src/workers/aruco-worker.js`, `edit/src/workers/laser-worker.js`):**
     - **Reciclagem Ping-Pong de Memória e ROI Recortada:** Workers agora devolvem `recycledBuffer` via lista `Transferable`; após o lock do alvo e fora da aba Câmera, `extractLaserRoiImageData()` recorta a ROI direto do `<video>` em `roiCanvas` sem ler 1280x720 na CPU a cada frame.
     - **Desbloqueio de FPS no Android (ex: Samsung A32):** Adicionado `requestVideoFrameCallback` (com fallback `rAF`), removida dupla limitação em `detectLaserImpulse()`, aplicadas restrições `frameRate: { ideal, min }` com fallback, trava opcional de hardware (`focusMode` / `whiteBalanceMode` manuais) em `applyCameraHardwareLock()`, telemetria real `track.getSettings()` e seletor `#resolution-select` (`1280x720`, `960x540`, `640x480`) em `Ajustes`.
     - **Cadências Rápidas (150ms–220ms+):** Reduzido `cooldownMs` de `250ms` para `105ms`, adicionado disparo por borda de subida (`wasLaserActive` / `laserWasActive`), proteção de halo no `bgBuffer` e reativação rápida para disparos em pontos distintos (`>= 35px` a partir de `90ms`) mesmo em câmeras a ~14 FPS.
  4. **Testes Automatizados (`test/test_backend_and_cadence.py`):**
     - Criada bateria validando certificado SAN, requisições HTTPS concorrentes com Gzip, bloqueio de path traversal e detecção de cadências de 150ms–220ms no `laser-worker.js`.
* **Como Validar:**
  - Executar `.venv/bin/python3 -m unittest discover -s test -v` e `.venv/bin/python3 edit/tools/gerenciador_projeto.py all`.
  - Testar no smartphone (ex: Samsung A32) alternando a resolução para `960x540` ou `640x480` em `Ajustes` e efetuando disparos em cadência rápida de 150ms–220ms.

### 2026-11-13 (Etapa 3) — Zoom Híbrido (1x–4x), Snapshot Limpo Sem Overlay ArUco, Padrão 15 FPS + VGA e Auto-Lock 350ms
* **O que foi feito:**
  1. **Padrão Universal `15 FPS` + `640x480 (VGA)` e Cadência Rápida (`edit/src/index.html`, `edit/src/app.js`, `edit/src/workers/laser-worker.js`):**
     - Definidos `15 FPS` e `640x480 (VGA)` como opções padrão selecionadas ao abrir o app (mantendo `10/15/30/60 FPS`, `960x540` e `1280x720` disponíveis em `Ajustes`).
     - Ajustado `cooldownMs` para `95ms`, `slackMs` de 15 FPS para `10ms` (evitando descarte de quadros por *jitter*), e limiar espacial proporcional `minSpotDist = Math.max(14, Math.round(roiW * 0.045))` no worker e no fallback para cadências de 150ms–220ms em VGA.
  2. **Zoom Híbrido `1x` a `4x` na Câmera (`edit/src/index.html`, `edit/src/style.css`, `edit/src/app.js`):**
     - Adicionada barra `#camera-zoom-bar` abaixo do visor com botões rápidos (`1x`, `1.5x`, `2x`, `3x`) e slider contínuo (`1.0x`–`4.0x`) em conformidade com `Regras/06_regras_de_visual.md`.
     - Implementada `applyCameraZoom()` usando `capabilities.zoom` via hardware quando disponível e recorte digital centralizado (`drawCameraView()` / `extractLaserRoiImageData()`), travando os controles de zoom durante o lock do alvo.
  3. **Snapshot Retificado 100% Limpo e Folha Completa (`edit/src/app.js`, `edit/src/lib/aruco_universal.js`, `lib/aruco_universal.js`):**
     - `warpTargetView(true)` redesenha um quadro limpo direto do `<video>` (`drawCameraView`) antes de ler pixels em `cameraCtx`, eliminando os contornos verdes e rótulos `ID:X` do snapshot da aba `Alvo`.
     - `ArUcoUniversalDetector.identifyTargetCorners()` agora retorna também `outerCorners` (`_extractOuterCorners`), preservando a folha inteira até a borda externa dos 4 ArUcos no recorte `600x800`.
  4. **Auto-Lock Facilitado (`350ms`) e Indicadores nos 4 Cantos (`edit/src/index.html`, `edit/src/style.css`, `edit/src/app.js`):**
     - Reduzido `autoLockDurationMs` para `350ms` com tolerância de movimento de `28px` para facilitar trava com zoom ativo.
     - Adicionada `updateGuideCorners()` para destacar em verde cada cantoneira `.guide-corner` conforme os marcadores entram no enquadramento.
  5. **Testes (`test/test_backend_and_cadence.py`, `test/test_aruco_universal.py`):**
     - Adicionada validação para cadência rápida de 150ms–220ms em ROI VGA (`~20px`) e asserção para `outerCorners` nos alvos Mantis.
* **Como Validar:**
  - Executar `.venv/bin/python3 -m unittest discover -s test -v` e `.venv/bin/python3 edit/tools/gerenciador_projeto.py all`.
  - Testar na câmera do celular o zoom `1.5x`/`2x` em alvo distante, conferir o acendimento verde das cantoneiras no guia, travar os 4 ArUcos em ~350ms, verificar ausência de tracejos/IDs verdes na imagem retificada da Aba `Alvo` e validar disparos rápidos a 150ms–220ms.

---

## [2026-11-18] - Testes de Performance, Otimização Big-O e Estratégias de Caching

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Medir e otimizar a complexidade algorítmica (Big-O), eliminar gargalos de rede/TCP/CPU e implantar caching em múltiplas camadas no back-end e motor edge sem custos extras de infraestrutura.
* **Modificações Realizadas:**
  1. **Back-end HTTP/1.1 Keep-Alive, Cache em RAM e `ETag` (`edit/tools/servidor_dev.py`):**
     - Habilitado `protocol_version = "HTTP/1.1"` com `wbufsize = 65536` e `disable_nagle_algorithm = True` (`TCP_NODELAY`), eliminando a penalidade de ~40ms de *TCP Delayed ACK* em conexões Keep-Alive.
     - Adicionado cache em memória (`_TARGETS_CACHE`) para `GET [OCULTO]` (versões JSON e Gzip) invalidado em $\mathcal{O}(1)$ por `st_mtime_ns` de `MANTIS_IMAGES_DIR`.
     - Adicionado cache LRU em RAM (`_STATIC_MEM_CACHE`, limite 16 MB para arquivos até 2 MB) indexado por `(path, st_mtime_ns, st_size)`.
     - Substituído `Cache-Control: no-cache, no-store, must-revalidate` por `Cache-Control: no-cache, must-revalidate` + `ETag` e suporte a `If-None-Match` (`304 Not Modified`).
  2. **Memoização $\mathcal{O}(1)$ e Atualização Incremental $\mathcal{O}(S)$ (`edit/src/ballistics_echarts.js`):**
     - Criada `_computeSpatialSummary()` para memoizar métricas espaciais (`extremeSpread`, `meanRadius`, `zoneCounts`, `totalPoints`) durante o loop `tickChrono()` a 60 FPS (reduzindo 120 frames com 500 tiros de `376.81ms` para `0.197ms`) e atualizar incrementalmente em $\mathcal{O}(S)$ ao adicionar um disparo.
  3. **Early-Skip no Motor de Laser (`edit/src/workers/laser-worker.js`):**
     - Adicionado desvio rápido para pixels com `r < 90 || deltaR < 10`, evitando avaliações espectrais redundantes na maior parte do frame mantendo paridade matemática.
  4. **Zero-GC e Cache Intra-Frame ArUco (`edit/src/lib/aruco_universal.js` e `lib/aruco_universal.js`):**
     - Reaproveitamento de `Float64Array` (`_gridSampleBuf`), projeção homográfica inline sem alocação de objetos `{x, y}`, cache de bits por `gridSize` em `quad._gridBitsCache` e otimização de `_bitsToBigInt()`.
  5. **Suíte de Benchmark (`test/test_performance_backend.py`) e Documentação (`Regras/07_performance_bigo_e_caching.md`):**
     - Criação de testes automatizados para throughput HTTPS+Gzip, `ETag`/`304`, Keep-Alive, escalabilidade Big-O em Node.js e sincronização via `gerenciador_projeto.py all`.
* **Como Validar:**
  - Executar `.venv/bin/python3 -m unittest discover -s test -v` (15 testes passando).

---

## [2026-11-18] - Endurecimento Anti-Falso-Positivo na Detecção de Disparos e Trava de Exposição

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Eliminar falsos positivos em disparos causados por autoexposição, flicker de iluminação quente, ruído pontual de sensor, vibração mecânica de tripé e duplo disparo por arrasto de gatilho, preservando a complexidade $\mathcal{O}(W_{\text{roi}} \cdot H_{\text{roi}} / 4)$ Zero-GC e cadências rápidas de 150ms–220ms.
* **Modificações Realizadas:**
  1. **Trava de Autoexposição (AE Lock) e Subexposição Automática (`edit/src/app.js`, `edit/src/index.html`):**
     - `applyCameraHardwareLock()` agora trava `exposureMode` (`"manual"` ao travar o alvo, `"continuous"` ao destravar) e aplica `applyHardwareExposure(state.exposureDimming)`.
     - Corrigido o cálculo de `applyHardwareExposure()` para interpolar de `0 EV` (neutro) até `minExp` (subexposição) em vez de `maxExp`.
     - Adicionada janela de estabilização pós-lock (`laserWarmupUntil`, 350ms) em `lockTargetWithCorners()` para evitar disparo fantasma na transição de brilho do hardware.
     - Adicionado controle `#slider-exposure` ("SUBEXPOSIÇÃO DA CÂMERA (ANTI-REFLEXO)", padrão `65%`) na aba `Ajustes`.
  2. **Filtros Espaciais e Espectrais Zero-GC (`edit/src/workers/laser-worker.js`, `edit/src/app.js`):**
     - Exigido agrupamento mínimo (`hitCount >= 3`) e verificação de compacidade espacial via bounding-box (`spanX, spanY <= maxSpotSpan` em $\mathcal{O}(1)$ de memória) para rejeitar hot pixels, vibrações de borda e passagem de objetos.
     - Endurecidos os critérios espectrais `isDarkZoneLaser`, `isSaturatedCore` e mapeamento `effPurity`, além de proteção contra arrasto de gatilho (`dt >= 120ms` para novo ponto distinto).
     - Elevados os padrões iniciais para `threshold: 215` e `colorPurity: 42` em `app.js` e `index.html`.
  3. **Testes Automatizados (`test/test_backend_and_cadence.py`):**
     - Adicionado `test_laser_worker_false_positive_rejection` validando rejeição de hot pixel, flicker de lâmpada quente/papel branco, vibração distribuída e arrasto de gatilho $< 120\text{ ms}$.
* **Como Validar:**
  - Executar `.venv/bin/python3 -m unittest discover -s test -v` e `.venv/bin/python3 edit/tools/gerenciador_projeto.py all`.


---

## [2026-11-19] - Refatoração do Ecossistema de Servidor, Pipeline Brotli/Gzip, Persistência Assíncrona WAL e Remoção Completa de QR Code

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Otimizar latência em rede local Wi-Fi, concorrência controlada, uso de memória zero-copy e persistência assíncrona em SQLite WAL, removendo integralmente códigos e assets de QR Code e mantendo 100% da calibração via marcadores ArUco.
* **Modificações Realizadas:**
  1. **Remoção Completa de QR Code (`edit/tools/servidor_dev.py`, `edit/src/app.js`, `edit/src/qrcode_conexao.png`):**
     - Removidos `atualizar_qrcode_conexao()`, imports/renderizações ASCII de `qrcode` e banner poluído no terminal (mantendo apenas URLs locais limpas no startup).
     - Removido o arquivo órfão `edit/src/qrcode_conexao.png` (refletido em `backup/src/`, `data/` e `android_app/www/`).
     - Removida a seção morta `// 9. Leitura de QR Code Auxiliar` (`BarcodeDetector` / `detectQRCodeCalibration`) e estados `qrCodeLastCheckTime` / `calibratedTargetProfile` em `edit/src/app.js`.
  2. **Concorrência Controlada e Otimização TLS (`edit/tools/servidor_dev.py`):**
     - `ThreadedHTTPSServer` refatorado e tipado com `ThreadPoolExecutor(max_workers=32)` em `process_request()` e encerramento limpo em `server_close()`.
     - Sobrescrito `DevRequestHandler.address_string()` para retornar `self.client_address[0]` diretamente sem *reverse DNS lookup*, mantendo `socket.settimeout(12.0)`.
     - Criado `create_ssl_context()` com `ssl.OP_NO_COMPRESSION`, TLS >= 1.2 e *Session Tickets* habilitados para reconexão rápida no Wi-Fi móvel.
  3. **Cache Zero-Copy (`memoryview`), Brotli (`.br`) e `Cache-Control` Inteligente (`edit/tools/servidor_dev.py`, `edit/tools/gerenciador_projeto.py`):**
     - `_read_static_cached()` retorna `memoryview` direto dos buffers em `_STATIC_MEM_CACHE`, evitando cópias extras em `wfile.write()`.
     - Suporte a `.br` (Brotli) com prioridade sobre `.gz` quando `Accept-Encoding` aceita `br`, alinhado com `comprimir_gz()` em `gerenciador_projeto.py` (gera `.br` quando o módulo `brotli` estiver instalado além dos `.gz`).
     - `Cache-Control: public, max-age=86400, immutable` para `/mantis_images/` e `no-cache, must-revalidate` com `ETag` para código-fonte e `[OCULTO]`.
  4. **Persistência Assíncrona Blindada (`[OCULTO]`) (`edit/tools/servidor_dev.py`):**
     - `SessionStore` dedicado com `queue.Queue(maxsize=256)`, *worker thread* em background e SQLite em `.cert/sessions.db` com `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=3000;`.
     - `POST [OCULTO]` valida `Content-Length <= 65536` antes da leitura (`413` se exceder, `400` se JSON inválido, `503` se fila cheia e `202 Accepted` em $\mathcal{O}(1)$).
     - `GET [OCULTO]` retorna sessões persistidas ordenadas da mais recente para a mais antiga, com drenagem limpa via sentinela em `server_close()` e `atexit`.
  5. **Testes Automatizados e Sincronização (`test/test_backend_and_cadence.py`):**
     - Adicionados testes validando ausência de QR Code/imports pesados, flags TLS, `Cache-Control: immutable`, prioridade `.br`, `POST`/`GET [OCULTO]`, backpressure `503`, `413`/`400` e *graceful drain* no SQLite WAL.
* **Como Validar:**
  - Executar `.venv/bin/python3 -m unittest discover -s test -v` e `.venv/bin/python3 edit/tools/gerenciador_projeto.py all`.

---

## [2026-11-19] - Implementação de Teste Escalatório de Usuários com Locust e Otimização de Alta Concorrência no Back-end

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Objetivo:** Implementar suíte de teste de carga escalatório com **Locust** (`10 -> 25 -> 50 -> 100` usuários simultâneos) para aferir e comprovar a eficiência, o throughput (RPS), os percentis de latência ($P_{50}, P_{95}, P_{99}$) e a resiliência da persistência SQLite WAL do back-end sob alta concorrência.
* **Modificações Realizadas:**
  1. **Ampliação de Concorrência Keep-Alive e Cache de Leitura SQLite WAL (`edit/tools/servidor_dev.py`):**
     - Expandido `DEFAULT_MAX_WORKERS` de `32` para `128` e `request_queue_size` de `64` para `256` em `ThreadedHTTPSServer`, eliminando inanição de threads quando mais de 32 clientes mantêm conexões persistentes `HTTP/1.1 Keep-Alive`.
     - Expandido `SESSION_QUEUE_MAXSIZE` para `1024` com drenagem em lotes de até `128` registros por transação SQLite WAL.
     - Adicionado cache em RAM (`_read_cache` e `_json_cache`) em `SessionStore.list_sessions_json_bytes()` invalidado atomicamente a cada commit do worker SQLite, servindo `GET [OCULTO]` em $\mathcal{O}(1)$ sem reabrir o banco entre lotes.
     - Adicionada flag `DevRequestHandler.quiet_logs` (`--quiet` / `-q`, além de `--port=` e `--workers=`) para eliminar contenção de I/O em `stderr` durante benchmarks de estresse.
  2. **Modelagem de Usuário e Curva Escalatória no Locust (`test/locustfile.py`):**
     - Implementado `TargetsVirtuUser(HttpUser)` simulando o fluxo completo do cliente: bootstrap de assets estáticos comprimidos (`.br`/`.gz`), revalidação condicional `ETag` (`304 Not Modified`), consulta ao catálogo `[OCULTO]` (`200` e `304`), download de imagens imutáveis (`/mantis_images/*`), submissão assíncrona de séries de tiros (`POST [OCULTO]` -> `202 Accepted`) e leitura de histórico (`GET [OCULTO]`).
     - Implementado `EscalatoryLoadShape(LoadTestShape)` com rampa automática em 4 degraus (`10 -> 25 -> 50 -> 100` usuários) e exportação automática de telemetria por estágio e por rota em `test/output/locust_step_report.json`.
  3. **Teste Automatizado e Gráfico de Escalabilidade (`test/test_locust_backend.py`, `requirements.txt`, `Regras/07_performance_bigo_e_caching.md`):**
     - Criado `test/test_locust_backend.py` integrando a execução headless do Locust à suíte `unittest`, gerando CSVs (`test/output/locust_backend_*.csv`), relatório JSON (`test/output/locust_step_report.json`) e gráfico comparativo (`test/output/locust_escalabilidade_backend.png`), além de validar `0%` de erros e persistência integral das sessões no SQLite WAL.
     - Atualizado `requirements.txt` com `locust>=2.40.0` e `brotli>=1.1.0` e gerados todos os pacotes `.br` e `.gz` em `data/`.
* **Como Validar:**
  - Executar `.venv/bin/python3 test/test_locust_backend.py -v` ou `.venv/bin/python3 -m unittest discover -s test -v` (19 testes passando).

---

## [2026-11-19] - Plano de Endurance Locust (1 Hora com 100 Usuários) e Redução Sub-10ms do Tempo de Requisição

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Status:** Concluído e Validado (4/4 etapas executadas)
* **Objetivo:** Sustentação de teste de resistência (*Soak/Endurance Test*) por **1 hora (`3600s`) com `100` usuários simultâneos** no Locust e redução drástica da latência de requisição (de $P_{50} \approx 50\text{ ms} / P_{95} \approx 130\text{ ms}$ para $P_{50} \le 10\text{ ms}$ com 100 usuários concorrentes).
* **Diagnóstico dos Gargalos Atuais:**
  1. **Excesso de Syscalls de Filesystem por Request (`edit/tools/servidor_dev.py`):** Cada requisição estática executa até 3 chamadas `Path.resolve()` (`realpath` no kernel) e 4 a 6 chamadas `stat()`/`is_file()`/`is_dir()`, mesmo quando o payload já reside em RAM ou retorna `304 Not Modified` (~5.000 syscalls/s sob 600 req/s).
  2. **Overhead de Parsing por Requisição:** `mimetypes.guess_type()` e parsing completo de `Accept-Encoding` com `split`/`float` rodam a cada request.
  3. **Overhead do Cliente Gerador (`HttpUser` vs `FastHttpUser`):** `python-requests` + `urllib3` em `HttpUser` consome alta fatia de CPU sob 100 greenlets simultâneos no mesmo host, inflando a latência medida pelo cliente; `FastHttpUser` (`geventhttpclient` em C) elimina o gargalo do lado do gerador.
  4. **Estabilidade de Disco em Teste de 1 Hora (`SessionStore` SQLite WAL):** Em 3600s com 100 usuários enviando `POST [OCULTO]`, o banco pode acumular $> 150.000$ registros se não houver *checkpoint* automático (`PRAGMA wal_autocheckpoint`) e política de janela deslizante (*ring buffer* configurável de retenção máxima de linhas).
* **Plano de Implementação (4 Etapas — Concluído):**
  1. **Etapa 1 — Fast-Path Zero-Syscall em RAM (`edit/tools/servidor_dev.py`):**
     - Implementado `_ROUTE_FAST_CACHE` (`OrderedDict`, 512 entradas / 32 MB) indexado por `(path_clean, enc_preference)` via `_RouteFastEntry`, com TTL curto (`1.0s` em dev) e permanente em `--gzip`, além de `_ACCEPT_ENCODING_CACHE` e cache zero-syscall em `_get_targets_cached()`.
     - Validado por `test_route_fast_cache_zero_syscall_hot_path` (**0 syscalls** `Path.resolve`, `Path.stat`, `Path.is_file`, `Path.is_dir` e `os.stat` para `200 OK` e `304 Not Modified`).
  2. **Etapa 2 — Proteção de Endurance 1h no SQLite WAL (`edit/tools/servidor_dev.py`):**
     - Configurados `PRAGMA wal_autocheckpoint=1000;` e `PRAGMA journal_size_limit=67108864;` (64 MB) em `SessionStore._configure_connection`.
     - Implementada retenção deslizante configurável (`MAX_STORED_SESSIONS = 10000`, `SESSION_PRUNE_EVERY_N_COMMITS = 16`) e fila `SESSION_QUEUE_MAXSIZE = 4096`.
     - Validado por `test_session_store_wal_pragmas_queue_and_sliding_retention`.
  3. **Etapa 3 — Migração para `FastHttpUser` e Perfil de 1 Hora (`3600s`) com 100 Usuários (`test/locustfile.py`):**
     - Migrado `TargetsVirtuUser` para `FastHttpUser` (`geventhttpclient`, `insecure=True`, `concurrency=8`).
     - Atualizado `EscalatoryLoadShape` para modo **Endurance 1h (`3600s`)** por padrão (`10 -> 25 -> 50 -> 100` nos primeiros `60s`, sustentando `100` usuários até `3600s`), preservando `LOCUST_STAGE_DURATION` e `LOCUST_TOTAL_DURATION` para execução rápida (`12s`) no CI.
  4. **Etapa 4 — Validação Comparativa e Atualização de Métricas (`test/test_locust_backend.py` e `Regras/07_performance_bigo_e_caching.md`):**
     - Atualizados os SLAs em `test/test_locust_backend.py` (`total_rps >= 500`, `p50_ms <= 20`, `p95_ms < 85`) e adicionado `test_endurance_1h_shape_configuration`.
     - Resultados comparativos observados no teste escalatório (`test/output/locust_step_report.json` e `test/output/locust_escalabilidade_backend.png`):
       - **Antes (`HttpUser`):** `6.854` reqs | `559.5 req/s` | `P50=36 ms` | `P95=130 ms` | `0.00%` erros.
       - **Depois (4 Etapas):** `11.522` reqs | `949.4 req/s` (Estágio 4 com 100 usuários: `1286.6 req/s`) | `P50=10 ms` (`8–9 ms` nas rotas estáticas/ETag/POST) | `P95=52 ms` | `0.00%` erros.
* **Como Validar:**
  - Suíte completa: `.venv/bin/python3 -m unittest discover -s test -v` (22 testes passando).
  - Endurance 1h interativo/headless: iniciar `.venv/bin/python3 edit/tools/servidor_dev.py --gzip --quiet` e executar `.venv/bin/locust -f test/locustfile.py --host https://127.0.0.1:8443` (padrão `3600s` com `100` usuários).

---

## [2026-11-23] - Execução do Plano de Endurance Locust (1.000 Usuários / 1 Hora) e Otimização Clusterizada do Back-end

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Status:** Concluído e Validado (5/5 etapas executadas conforme `Regras/08_plano_endurance_locust_1000_usuarios.md`)
* **Objetivo:** Escalar o back-end HTTPS de `100` para **`1.000` usuários simultâneos** (taxa de spawn `100 usuários/s`, endurance padrão `3600s`) com tolerância estrita de zero erros (`0.00%`), arquitetura stateless e persistência SQLite WAL particionada.
* **Modificações Realizadas:**
  1. **Etapa 1 — Cache Híbrido L1 (RAM Zero-Copy) + L2 (Redis com Fallback Transparente) (`edit/tools/servidor_dev.py`, `test/test_cache_backend.py`):**
     - Implementados `CacheBackend` e cliente TCP RESP2 leve `_MinimalRedisClient` com integração opcional via `REDIS_URL` ou `127.0.0.1:6379` e circuit-breaker com fallback transparente para RAM local (`memoryview`).
     - Armazenamento e revalidação de `ETag` (`304 Not Modified`) para `[OCULTO]` e `[OCULTO]` com invalidação distribuída via chave versionada `[OCULTO]`.
  2. **Etapa 2 — Índices Compostos, Fila 16k, Lotes 512 e Sharding (`edit/tools/servidor_dev.py`, `test/test_backend_and_cadence.py`):**
     - Criados os índices `idx_sessions_target_id_desc`, `idx_sessions_session_id` e `idx_sessions_created_at`, além dos pragmas `PRAGMA mmap_size=268435456` e `PRAGMA temp_store=MEMORY`.
     - Elevados `SESSION_QUEUE_MAXSIZE = 16384` e `SESSION_BATCH_SIZE = 512`; implementado particionamento sob demanda via `TARGETS_DB_SHARDS` (`sessions_shard_N.db`) com roteamento por hash `blake2b` e consulta agregada/filtrada (`?target=`).
  3. **Etapa 3 — Motor Concorrente 1.000+ Conexões, `[OCULTO]`, Stateless e `SO_REUSEPORT` (`edit/tools/servidor_dev.py`, `test/test_backend_and_cadence.py`):**
     - Elevado `request_queue_size = 2048`, `DEFAULT_MAX_WORKERS = 1536`, criado `DynamicWorkerPool` elástico (escala sob rajadas Keep-Alive e retrai após `30s` ociosos), handshake TLS paralelo (`do_handshake_on_connect=False`), elevação de `RLIMIT_NOFILE` e guarda de pressão Keep-Alive.
     - Adicionado `GET [OCULTO]` em $\mathcal{O}(1)$ (`workers`, `queue`, `cache`, `stateless: true`) e cluster multi-processo `SO_REUSEPORT` (`--processes=N` / `TARGETS_WORKERS_PROC` via `_worker_process_main`).
  4. **Etapa 4 — Locustfile 1.000 Usuários / 1h e Validação Headless (`test/locustfile.py`, `test/test_locust_backend.py`):**
     - Atualizado `EscalatoryLoadShape` para rampa `100 -> 250 -> 500 -> 1000` usuários (`spawn_rate = 100/s`, `3600s` padrão) e adicionadas tarefas de ping em `/` e `[OCULTO]`.
     - Atualizado `test/test_locust_backend.py` para validar o cluster `SO_REUSEPORT` x 4 shards WAL com `--processes 4`, gerando [locust_step_report.json](file:///home/adm360virtu/Arduino/TARGETS_VIRTU/test/output/locust_step_report.json), CSVs e [locust_escalabilidade_backend.png](file:///home/adm360virtu/Arduino/TARGETS_VIRTU/test/output/locust_escalabilidade_backend.png) com `27.044` requisições, `1150.0 req/s` médios (`1624.6 req/s` em 500 usuários), `P50=19.0ms` (`3–4ms` em `304`/ping/`POST`), `P95=560.0ms` e **`0.00%` de erros**.
  5. **Etapa 5 — Documentação e Homologação (`Regras/07_performance_bigo_e_caching.md`, `Regras/roteiro_de_desenvolvimento_e_tarefas.md`, `Regras/historico_alteracoes.md`):**
     - Atualizados os SLAs, complexidade Big-O, checklist do roteiro e histórico formal de alterações.
* **Como Validar:**
  - Executar `.venv/bin/python3 -m unittest discover -s test -v` (29 testes aprovados).
  - Executar `.venv/bin/python3 test/test_locust_backend.py -v` para gerar o relatório JSON e gráfico comparativo de 100 a 1.000 usuários em `test/output/`.

---

## [2026-11-24 12:09] - Execução do Plano de Mitigação de Latência de Cauda (P95) e Saturação (500 a 1.000 Usuários)

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Status:** Concluído e Homologado (5/5 etapas executadas conforme `Regras/09_plano_mitigacao_p95_e_saturacao_1000_usuarios.md`)
* **Motivo da Alteração:** Eliminar a saturação observada entre `500` e `1.000` usuários simultâneos (`643,01 req/s`, $P_{95}=1.000\text{ ms}$, `GET [OCULTO]` $P_{95}=2.500\text{ ms}$) causada por criação síncrona de threads na rampa, retenção de threads em conexões `Keep-Alive` ociosas (`12s`), contenção de locks em `[OCULTO]` e recomputação de `time.gmtime()` por resposta.
* **Modificações Realizadas:**
  1. **Etapa 1 — Backlog TCP `4096`, Pre-Warming e Opções de Socket (`edit/tools/servidor_dev.py`, `test/test_backend_and_cadence.py`):**
     - Elevado `SOCKET_BACKLOG_SIZE` de `2048` para `4096` em `ThreadedHTTPSServer.request_queue_size`.
     - Implementado pre-warming de `min_workers` threads diretamente em `DynamicWorkerPool.__init__()`.
     - Adicionado `ThreadedHTTPSServer.get_request()` + `_configure_client_socket()` aplicando `TCP_NODELAY` e `SO_KEEPALIVE` imediatamente no `accept()` (antes do handshake TLS), além de `TCP_NODELAY` no socket de escuta em `server_bind()`.
  2. **Etapa 2 — Governança Adaptativa de `HTTP/1.1 Keep-Alive` (`edit/tools/servidor_dev.py`, `test/test_backend_and_cadence.py`):**
     - Reduzido `KEEPALIVE_PRESSURE_RATIO` de `0.90` para `0.75`, incluído cabeçalho `Keep-Alive: timeout=5, max=100` em respostas persistentes e sinalização limpa com `Connection: close` sob pressão (`>= 75%`) ou após `100` requisições.
     - Adicionado timeout adaptativo (`KEEPALIVE_PRESSURE_TIMEOUT_SECONDS = 1.5s` quando utilização `> 50%`) entre requisições Keep-Alive e dimensionamento padrão `DEFAULT_MAX_WORKERS = 2048` / `DEFAULT_CLUSTER_MAX_WORKERS = 1024`.
  3. **Etapa 3 — Fast-Path Lock-Free `[OCULTO]` e Cache `Date` 1 Hz (`edit/tools/servidor_dev.py`, `test/test_backend_and_cadence.py`):**
     - Adicionado `HEALTH_CACHE_TTL_SECONDS = 0.05` (micro-TTL de `50 ms`) com `ThreadedHTTPSServer.health_snapshot_bytes()` servindo JSON pré-serializado via `memoryview` sem adquirir locks no hot-path e invalidação imediata no `close()` do servidor ou do `SessionStore`.
     - Adicionado `DynamicWorkerPool.utilization_counts()` sem lock para leitura no hot-path, sobrescrito `DevRequestHandler.date_time_string()` com cache `1 Hz` e corrigida a precedência de `PRAGMA busy_timeout=3000;` antes de `PRAGMA journal_mode=WAL;` em `SessionStore._configure_connection()`.
  4. **Etapa 4 — Calibração Locust e Validação de SLA (`test/test_locust_backend.py`, `test/locustfile.py`):**
     - Ajustado `test/test_locust_backend.py` para `DEFAULT_CLUSTER_MAX_WORKERS` (`1024` workers/proc) e configurado `TargetsVirtuUser.client_pool` (`HTTPClientPool` compartilhado com `LOCUST_POOL_CONCURRENCY=64`) em `test/locustfile.py`.
     - Resultados comparativos observados em `test/output/locust_step_report.json`:
       - **Antes:** `14.062` reqs | `643,01 req/s` | $P_{50}=24\text{ ms}$ | $P_{95}=1.000\text{ ms}$ | $P_{99}=2.100\text{ ms}$ | `GET [OCULTO]` $P_{95}=2.500\text{ ms}$ | `0,00%` erros.
       - **Depois:** **`32.464`** reqs | **`1.330,92 req/s`** (Estágio 3: `1.801,24 req/s`, Estágio 4: `1.750,18 req/s`) | $P_{50}=30\text{ ms}$ (Estágio 1: `3 ms`, Estágio 2: `2 ms`) | $P_{95}=580\text{ ms}$ | $P_{99}=960\text{ ms}$ | `GET [OCULTO]` $P_{95}=710\text{ ms}$ (média `123,54 ms`) | **`0,00%` erros** (`0` falhas, drenagem integral nos 4 shards WAL).
  5. **Etapa 5 — Homologação e Documentação (`Regras/07_performance_bigo_e_caching.md`, `Regras/roteiro_de_desenvolvimento_e_tarefas.md`, `Regras/09_plano_mitigacao_p95_e_saturacao_1000_usuarios.md`, `Regras/historico_alteracoes.md`):**
     - Suíte completa executada com 100% de aprovação (`31` testes em `43.142s`) e artefatos regenerados em `test/output/`.
* **Como Validar:**
  - Executar `.venv/bin/python3 -m unittest discover -s test -v` (`31` testes aprovados).
  - Executar `.venv/bin/python3 test/test_locust_backend.py -v` para regenerar [locust_step_report.json](file:///home/adm360virtu/Arduino/TARGETS_VIRTU/test/output/locust_step_report.json) e [locust_escalabilidade_backend.png](file:///home/adm360virtu/Arduino/TARGETS_VIRTU/test/output/locust_escalabilidade_backend.png).

---

## [2026-11-24 12:32] - Execução e Diagnóstico do Teste de Estresse de 10.000 Usuários (Zero Modificação no Back-end)

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Status:** Concluído, Diagnosticado e Documentado (conforme `Regras/10_plano_estresse_10000_usuarios_zero_modificacao.md`)
* **Motivo da Alteração:** Avaliar empiricamente a resposta do cluster `SO_REUSEPORT` sob escala 10x maior (10.000 usuários simultâneos) sem nenhuma alteração no código de produção (`edit/tools/servidor_dev.py` 100% inalterado), observando a resiliência do Keep-Alive adaptativo, backlog TCP (`4096`), teto de threads ($6.144$) e SQLite WAL particionado.
* **Resultados e Métricas Observadas:**
  1. Executado teste de estresse escalatório (`test/test_locust_10000_users.py`) com rampa acelerada `1.000 -> 2.500 -> 5.000 -> 10.000` usuários a `250/s` (6 processos Locust + 6 processos cluster HTTPS).
  2. Total de **`65.105` requisições** processadas em 32 segundos.
  3. Taxa de falhas: **`0,00%`** (`0` erros em 65.105 requisições sob 10.000 conexões simultâneas).
  4. Throughput médio global: **`1.410,72 req/s`** (pico de `1.660,01 req/s` em 5.000 usuários e `1.282,62 req/s` em 10.000 usuários).
  5. Mediana global ($P_{50}$): **`260 ms`** (estável entre `210 ms` e `260 ms`).
  6. Latência de cauda ($P_{95}$): `4.800 ms` | $P_{99}$: `18.000 ms`. A cauda reflete o tempo de espera no backlog TCP do kernel para conexões excedentes à capacidade nominal de threads do cluster ($6.144$ workers vs. $10.000$ clientes concorrentes), amortecida com sucesso pelo buffer de $24.576$ slots do kernel sem quedas de conexão.
  7. Drenagem SQLite WAL: **`1.365` sessões gravadas com 100% de integridade** nos 4 shards (`sessions_shard_0..3.db`).
* **Como Validar:**
  - Executar `.venv/bin/python3 test/test_locust_10000_users.py -v`.
  - Inspecionar [locust_10000_step_report.json](file:///home/adm360virtu/Arduino/TARGETS_VIRTU/test/output/locust_10000_step_report.json), [locust_10000_backend_stats.csv](file:///home/adm360virtu/Arduino/TARGETS_VIRTU/test/output/locust_10000_backend_stats.csv) e [locust_10000_escalabilidade.png](file:///home/adm360virtu/Arduino/TARGETS_VIRTU/test/output/locust_10000_escalabilidade.png).











---

## [2026-11-25 08:05] - Etapa 1 do Plano de Front-end: Suíte de Diagnóstico Automatizado (`test/test_frontend_diagnostics.py`)

* **Responsável:** Equipe de Desenvolvimento (Antigravity & Usuário)
* **Status:** Concluído (conforme `Regras/11_plano_diagnostico_e_melhoria_frontend.md`, Etapa 1). **Nenhum arquivo de `edit/src/` foi modificado.**
* **Motivo da Alteração:** Medir e validar com testes automatizados os 8 pontos críticos do diagnóstico do front-end antes de qualquer refatoração, criando guardas de regressão para as Entregas 1 a 5.
* **O que foi criado:**
  1. `test/test_frontend_diagnostics.py` (`unittest` + Node.js `vm`), com 41 testes em 5 frentes: Regras Proibitivas (Regras/05 e 06), integridade DOM ↔ JS, invariantes geométricas (resolução/flip/zoom/ROI), benchmark de `warpTargetView()` e ciclo de vida das rodadas, e contrato `POST/GET [OCULTO]` contra o `ThreadedHTTPSServer` real.
  2. As funções de `app.js` são extraídas do código-fonte real e executadas isoladamente com stubs (sem navegador), garantindo que os testes reflitam o código em produção.
  3. Defeitos confirmados ficam marcados com `@unittest.expectedFailure` e vinculados à sua Entrega; ao serem corrigidos, produzem *unexpected success*, forçando a promoção a guarda de regressão.
  4. Relatório consolidado em `test/output/frontend_diagnostics_report.json`.
* **Principais medições:** 25 guardas aprovadas, 16 defeitos confirmados; homografia obsoleta gera 729 px (resolução) / 480 px (flip) / 438 px (zoom) de erro; `warpTargetView` ~181 ms com 480.000 chamadas a `projectPoint` (referência inline Zero-GC: ~9,6 ms); ROI do laser alinhada (0,0 px — item 1.4.2 refutado); novo achado: `previousRound` nunca é atribuído.
* **Como Testar:**
  - `.venv/bin/python3 -m unittest discover -s test -p test_frontend_diagnostics.py -v` → `OK (expected failures=16)`.
  - `.venv/bin/python3 -m unittest discover -s test -v` para a suíte completa.
