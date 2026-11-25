# Plano de Estresse e Saturação Empírica: 10.000 Usuários Simultâneos (Zero Modificação no Back-end)

Este documento estabelece o plano técnico para executar um teste de estresse escalatório extremo de **1.000 até 10.000 usuários simultâneos**, mantendo o código do back-end (`edit/tools/servidor_dev.py`) **estritamente sem nenhuma modificação**, com o objetivo de observar e auditar empiricamente os limites físicos da arquitetura atual.

---

## 1. Premissa e Filosofia do Teste

* **Zero Modificação no Código do Servidor:** `edit/tools/servidor_dev.py` permanece idêntico à versão estável homologada na Etapa 5 do plano anterior (`SOCKET_BACKLOG_SIZE = 4096`, `DEFAULT_CLUSTER_MAX_WORKERS = 1024`, Keep-Alive adaptativo com ratio `0.75` e timeout `1.5s`, micro-TTL de `50 ms` em `[OCULTO]`, cache `Date` de 1 Hz, 4 shards SQLite WAL e 6 processos `SO_REUSEPORT`).
* **Objetivo de Descoberta Empírica:** Mapear o comportamento do sistema sob carga 10x maior que a meta inicial:
  1. A capacidade nominal máxima de threads do cluster é de $6 \text{ processos} \times 1.024 \text{ workers} = 6.144\text{ threads}$. Como o cluster se comportará quando o volume de clientes simultâneos ($10.000$) ultrapassar em $63\%$ o número total de workers?
  2. O mecanismo de governança de Keep-Alive (reciclagem limpa com `Connection: close` a 75% e timeout de 1.5s) conseguirá reciclar threads rápido o bastante para evitar esgotamento de conexões?
  3. O backlog TCP do kernel ($6 \times 4.096 = 24.576$ conexões pendentes em buffer) sustentará o enfileiramento sem gerar `Connection refused` ou timeouts de handshake TLS?
  4. Qual será o throughput real (req/s), o percentil de cauda ($P_{95}$ e $P_{99}$) e a taxa de falha sob 10.000 clientes simultâneos?
  5. Os 4 shards SQLite WAL manterão a integridade de gravação para milhares de séries de tiros enviadas em rajada contínua?

---

## 2. Desenho da Carga Escalatória (1.000 → 2.500 → 5.000 → 10.000 Usuários)

Para isolar o momento exato em que a saturação ou estrangulamento se manifesta, o teste divide-se em **4 estágios de 8 segundos cada** (duração total: 32 segundos), com taxa de injeção acelerada de **`250 usuários/s`**:

```text
Usuários Simultâneos
    │
10k ┼───────────────────────────────────────────────────────────── [Estágio 4: 10.000u]
    │                                                            (Teste de Quebra/Sobrecarga)
    │                                              ┌─────────────┘
 5k ┼──────────────────────────────── [Estágio 3: 5.000u]
    │                                (81% do Teto de Workers)
    │                  ┌─────────────┘
2.5k┼── [Estágio 2: 2.500u]
    │   (40% do Cluster)
 1k ┼── [Estágio 1: 1.000u] (Baseline Homologado)
    └───┴──────────────┴─────────────┴─────────────┴─────────────► Tempo (s)
        0s            8s            16s           24s           32s
```

| Estágio | Usuários | Taxa de Injeção | Duração | Hipótese Teórica / Foco de Observação |
| :---: | :---: | :---: | :---: | :--- |
| **Estágio 1** | `1.000` | `250/s` | `8s` | **Baseline Estável:** Deve replicar o comportamento conhecido ($P_{50} \le 30\text{ ms}$, Throughput $\ge 1.000\text{ req/s}$, 0 falhas). |
| **Estágio 2** | `2.500` | `250/s` | `8s` | **Carga Moderada:** Ocupação esperada de ~40% do pool total de workers ($2.500 / 6.144$). Throughput deve subir para ~1.500–2.000 req/s. |
| **Estágio 3** | `5.000` | `250/s` | `8s` | **Zona Crítica:** Ocupação atinge ~81% do pool. A governança de Keep-Alive deve acionar fechamento ativo (`Connection: close`) para liberar sockets. |
| **Estágio 4** | `10.000` | `250/s` | `8s` | **Sobrecarga Extrema:** 10.000 clientes para 6.144 threads ($1,63\times$ capacidade). Testa a retenção no backlog TCP, latência de fila e taxa de erros. |

---

## 3. Arquitetura de Execução do Teste

* **Servidor em Teste:**
  - Cluster `SO_REUSEPORT` com 6 processos workers independentes compartilhando a porta local.
  - Teto de `1024` threads por processo ($6.144$ workers no total).
  - 4 Shards SQLite em modo WAL particionados por hash `blake2b` (`sessions_shard_0..3.db`).
  - Cache híbrido L1 (RAM Zero-Copy) + L2 (`CacheBackend`).
* **Gerador de Carga (Locust):**
  - Script isolado: `test/test_locust_10000_users.py` (preservando o teste padrão de regressão `test/test_locust_backend.py`).
  - 6 processos trabalhadores Locust (`--processes 6`) para distribuir 10.000 greenlets nos 12 núcleos do processador host.
  - Limite de file descriptors (`RLIMIT_NOFILE`) elevado para `65536`.
  - Artefatos dedicados gerados em `test/output/`:
    - `locust_10000_step_report.json`
    - `locust_10000_backend_stats.csv`
    - `locust_10000_backend_stats_history.csv`
    - `locust_10000_escalabilidade.png`

---

## 4. Métricas e Questões a Serem Respondidas

1. **Throughput de Ruptura:** O cluster consegue manter ou superar $1.500\text{ req/s}$ no Estágio 4 ou há degradação acentuada por concorrência de CPU e context switching?
2. **Latência de Cauda ($P_{95}$ e $P_{99}$):** Como a fila de aceitação do kernel e o tempo de espera de thread afetam os tempos de resposta sob 10.000 conexões?
3. **Taxa de Erro:** Ocorrerão erros de socket (`ConnectionResetError`, `TimeoutError`, `BrokenPipe`) ou a governança Keep-Alive absorverá toda a pressão?
4. **Resiliência do SQLite WAL:** As 4 filas de shards conseguirão processar todas as inserções sem estourar o limite de 16k (`queue.Full`)?

---

## 5. Resultados Empíricos Observados (Execução em 2026-11-24)

O teste foi executado com o cluster intacto (6 processos `SO_REUSEPORT`, 4 shards SQLite WAL, zero alterações no servidor).

### 5.1. Métricas Coletadas

* **Volume Total de Requisições (32s):** `65.105` requisições processadas.
* **Taxa de Falha:** **`0,00%`** (`0` falhas em 65.105 requisições). Nenhuma conexão foi rejeitada ou sofreu `Connection reset`.
* **Throughput Médio Global:** **`1.410,72 req/s`** (pico de `1.660,01 req/s` em 5.000 usuários e `1.282,62 req/s` sob 10.000 usuários).
* **Mediana Global ($P_{50}$):** **`260 ms`** (estável entre `210 ms` e `260 ms` do início ao fim).
* **Latência de Cauda Global ($P_{95}$):** `4.800 ms` | **Percentil $P_{99}$:** `18.000 ms`.
* **Sessões Persistidas (SQLite WAL):** `1.365` requisições `POST [OCULTO]` com **100% de gravação confirmada** nos 4 shards (`sessions_shard_0..3.db`).

### 5.2. Desempenho por Estágio

| Estágio | Usuários | Requisições | Throughput (req/s) | Mediana ($P_{50}$) | Percentil $P_{95}$ | Percentil $P_{99}$ | Falhas |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Estágio 1** | `1.000` | `7.649` | `954,31 req/s` | `210 ms` | `990 ms` | `1.400 ms` | `0` |
| **Estágio 2** | `2.500` | `13.212` | `1.648,31 req/s` | `220 ms` | `2.100 ms` | `3.800 ms` | `0` |
| **Estágio 3** | `5.000` | `16.624` | `1.660,01 req/s` | `230 ms` | `3.300 ms` | `6.900 ms` | `0` |
| **Estágio 4** | `10.000` | `27.620` | `1.282,62 req/s` | `260 ms` | `4.800 ms` | `18.000 ms` | `0` |

---

## 6. Diagnóstico Técnico: Como o Sistema Agiu sob 10.000 Usuários

1. **Absorção Total sem Queda de Sockets (Zero Erros):**
   - O backlog TCP elevado na Etapa 1 (`SOCKET_BACKLOG_SIZE = 4096`) multiplicado pelos 6 processos `SO_REUSEPORT` forneceu um buffer de $24.576$ conexões no kernel.
   - Mesmo com 10.000 clientes disputando simultaneamente, nenhuma conexão extrapolou o buffer do SO, resultando em `0` falhas em `65.105` requisições.
2. **Comportamento da Governança de Keep-Alive sob Sobrecarga ($1,63\times$ o Teto de Workers):**
   - O teto de workers do cluster é de $6 \times 1.024 = 6.144\text{ threads}$.
   - Quando a concorrência atingiu 10.000 usuários, a governança adaptativa (Etapa 2) atuou em regime máximo: acionou o timeout agressivo de `1.5s` e emitiu `Connection: close` imediatamente a 75% de ocupação, reciclando conexões rapidamente e liberando workers para a próxima fila.
3. **Mecanismo da Latência de Cauda ($P_{95} = 4,8\text{ s}$ e $P_{99} = 18\text{ s}$):**
   - Como há 6.144 threads para 10.000 clientes, ~3.856 conexões concorrentes ficam aguardando no backlog do kernel a liberação de threads.
   - O tempo de espera na fila do kernel aumentou o tempo total de resposta de cauda ($P_{95}$), embora a mediana ($P_{50}$) tenha se mantido contida em `260 ms` para a grande maioria das requisições já aceitas.
4. **Competição de CPU no Host Local (12 Núcleos):**
   - Na mesma máquina de 12 núcleos rodaram: 6 processos do servidor HTTPS + 6 processos geradores do Locust + gevent event loops. A saturação de CPU local contribuiu diretamente para o aumento de latência percebido pelo cliente.
5. **Robustez do SQLite WAL:**
   - As filas assíncronas dos 4 shards mantiveram-se íntegras, sem nenhuma ocorrência de `queue.Full` ou `database is locked`.

