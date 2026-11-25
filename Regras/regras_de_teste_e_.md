# Guia de Regras e Testes: Integração do Mantis Target Analysis

Este documento estabelece as regras de estruturação de pastas, padronização de ambiente, fluxo de execução e critérios de validação para integrar o repositório [Mantis-Target-Analysis](https://github.com/raleighlittles/Mantis-Target-Analysis) na pasta `lib/` do seu projeto.
/home/adm360virtu/Arduino/TARGETS_VIRTU/lib/Mantis-Target-Analysis-main

---

## 1. Estrutura de Diretórios Recomendada

Mantenha a raiz do seu projeto isolada do código clonado para facilitar importações modulares e futuras atualizações:

```text
open-laser-target/
│
├── .venv/                      # Ambiente virtual Python
├── lib/
│   └── mantis_target_analysis/ # Repositório clonado/descompactado aqui
│       ├── __init__.py         # Certifique-se de existir para ser importável
│       ├── ...                 # Arquivos originais do repositório
│       └── requirements.txt    # Dependências do submódulo
│
├── tests/
│   ├── test_homografia.py      # Teste isolado de ArUco e perspectiva
│   └── test_agrupamento.py     # Teste isolado das métricas balísticas
│
├── main_test.py                # Script principal de teste do seu pipeline
├── requirements.txt            # Dependências unificadas do seu projeto
└── REGRAS_TESTE_INTEGRACAO.md  # Este arquivo
```

---

## 2. Regras de Ambiente e Instalação

1. **Isolamento via venv:** Nunca instale pacotes globalmente. Execute sempre com o ambiente virtual ativado:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # Linux

   ```

2. **Dependências Mínimas:** Instale as bibliotecas necessárias para OpenCV e análise matemática:
   ```bash
   pip install opencv-python opencv-contrib-python numpy matplotlib scipy
   ```

3. **Importação Modular:**
   Para importar as funções do repositório contido em `lib/`, garanta que a pasta `lib/mantis_target_analysis` contenha um arquivo vazio `__init__.py` ou configure o `sys.path` no topo do seu script de teste:
   ```python
   import sys
   from pathlib import Path

   # Adiciona a pasta lib ao caminho de módulos do Python
   BASE_DIR = Path(__file__).resolve().parent
   sys.path.append(str(BASE_DIR / "lib" / "mantis_target_analysis"))
   ```

---

## 3. Regras de Execução do Teste de Disparo

### Regra 1: Calibração de Hardware e Câmera
* **Fixação:** A câmera (webcam ou smartphone) deve estar montada em tripé ou superfície 100% rígida. Qualquer vibração descalibra a homografia calculada.
* **Exposição:** Configure a câmera para modo de subexposição proposital (`CAP_PROP_EXPOSURE` negativo ou modo manual). O papel deve aparecer acinzentado/escuro para que o ponto do laser gere um pico absoluto de saturação e brilho.

### Regra 2: Validação dos Marcadores ArUco
* O alvo impresso deve conter os 4 marcadores do dicionário `DICT_4X4_50` nos vértices com os IDs corretos:
  * ID 0: Canto superior esquerdo $(0, 0)$
  * ID 1: Canto superior direito $(W, 0)$
  * ID 2: Canto inferior direito $(W, H)$
  * ID 3: Canto inferior esquerdo $(0, H)$
* Apenas inicie a contagem de disparos se os 4 cantos forem detectados simultaneamente e a homografia for gerada com sucesso.

### Regra 3: Tratamento do Feixe de Laser
* **Espaço HSV:** Aplique o thresholding nas duas faixas de vermelho: $[0, 100, 200] \to [10, 255, 255]$ e $[160, 100, 200] \to [180, 255, 255]$.
* **Filtro de Área:** Apenas considere contornos com área entre $3$ e $250$ pixels para descartar ruído de sensor e reflexos de lâmpadas.
* **Debounce Temporal:** Bloqueie novas leituras por um intervalo de $250\text{ ms}$ a $300\text{ ms}$ após o primeiro impacto confirmado para evitar registros múltiplos do mesmo clique de gatilho.

---

## 4. Roteiro Passo a Passo para o Teste

1. **Baixar o repositório:**
   ```bash
   git clone https://github.com/raleighlittles/Mantis-Target-Analysis.git lib/mantis_target_analysis
   touch lib/mantis_target_analysis/__init__.py
   ```

2. **Criar script de teste integrado (`main_test.py`):**
   * Coletar as coordenadas $(X, Y)$ dos disparos através da câmera com o detector de laser.
   * Ao finalizar a série de tiros (pressionando a tecla `S`), enviar a lista de coordenadas para as rotinas de cálculo do repositório em `lib/`.

3. **Validação das Métricas Geradas:**
   * **Extreme Spread:** Verificar se a maior distância entre pares de furos bate com a régua física no papel calibrado.
   * **MPI (Mean Point of Impact):** Validar se o centroide médio do agrupamento condiz com a tendência visual dos disparos.
   * **Plotagem de Resultados:** Gerar e salvar o gráfico comparativo da série de tiros.

---

## 5. Critérios de Sucesso do Teste

| Critério | Meta Esperada | Ação Corretiva em Caso de Falha |
| :--- | :--- | :--- |
| **Detecção de Alvo** | 4 ArUcos identificados a $1,5\text{ m}$ | Melhorar iluminação indireta sobre o papel |
| **Taxa de Falso Positivo** | 0 disparos sem acionamento do laser | Subexpor a câmera e aumentar o threshold $V$ no HSV |
| **Debounce** | 1 registro exato por acionamento do gatilho | Aumentar o cooldown para $350\text{ ms}$ |
| **Métricas de Agrupamento** | Erro $< 2\text{ mm}$ no plano corrigido | Recalcular a escala de pixels por milímetro |