# Diretrizes de regras que não podem conter no front-end css e html

❌ Gradientes roxo e azul
❌ Cantos arredondados em excesso (acima de 4px)
❌ Tipografia Inter
❌ Botões em formato de pílula
❌ Efeitos de brilho (glow) e pulso
❌ Layout em formato Bento Grid
❌ Efeitos de vidro fosco (backdrop-filter)
❌ Títulos grandes e exageradamente em negrito (font-weight: 800/900)
❌ Formas 3D flutuantes e blobs radiais
❌ Ícones com contornos finos demais (abaixo de 2px)
❌ Textos com gradientes
❌ Imagens genéricas de banco de imagens
❌ Fundos repetidos e desordenados
❌ Sombras usadas em excesso
❌ Paletas de cores genéricas
E sem emojis.

---

## Arquitetura de Navegação por Abas (Estilo `MASTER_IPSC` Adaptado)
1. **Paleta de Cores (`MASTER_IPSC`):**
   - Tema Claro Padrão: `--bg-fundo: #f0f2f5`, `--bg-caixas: #ffffff`, `--texto-prin: #1a1a2e`, `--texto-sec: #637381`, `--borda: #e0e5ec`, `--primary: #4caf50`, `--secondary: #f57c00`, `--danger: #e53935`.
   - Modo Escuro (`body.dark-mode`): `--bg-fundo: #161c24`, `--bg-caixas: #212b36`, `--texto-prin: #f9fafb`, `--texto-sec: #919eab`, `--borda: #2d3748`.
2. **4 Abas Dedicadas (Zero Sobrecarga Visual):**
   - **Aba 1 (`tab-camera` - Câmera / Alinhamento):** Inicia com Card Central de Permissão de Câmera e seletor de lente. Após autorizar, exibe o visor da câmera com guia ArUco e o botão `RECALIBRAR ALVO`. Ao travar os 4 marcadores, transiciona automaticamente para a Aba 2.
   - **Aba 2 (`tab-alvo` - Alvo Retificado):** Exibe exclusivamente o alvo retificado ocupando a área útil e o botão `ZERAR`, sem métricas sobrepostas.
   - **Aba 3 (`tab-estatisticas` - Estatísticas / Tiros):** Painel de métricas balísticas (Total de Disparos, Split Time, Coordenada, Extreme Spread, Mean Radius), tabela cronológica de disparos e botão `ZERAR SÉRIE` (sem botões de exportação).
   - **Aba 4 (`tab-ajustes` - Ajustes do Sistema):** Área dedicada contendo seletor 30/60 FPS, chaves de Espelhar Imagem e Modo Escuro (`DARK`), e sliders de Sensibilidade de Brilho e Filtro R-G.
3. **Comportamento Mobile vs Desktop:**
   - **Mobile (`<= 1079px`):** Exibe estritamente 1 aba por vez controlada via `.bottom-nav` inferior com ícones SVG (`stroke-width >= 2px`).
   - **Desktop (`>= 1080px`):** Exibe layout em 2 colunas (`390px 1fr`) com navegação segmentada no `.site-header` superior, e grade dedicada quando na aba `Ajustes`.
