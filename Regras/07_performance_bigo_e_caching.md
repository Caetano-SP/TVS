# Diretrizes de Performance, Complexidade Big-O e Caching (TARGETS_VIRTU)

Este documento consolida as garantias de complexidade algorítmica, a arquitetura stateless/sharded e as estratégias de caching aplicadas ao back-end e ao motor edge do projeto.

---

## 1. Complexidade Algorítmica (Big-O Notation) por Módulo

| Módulo | Operação | Complexidade de Tempo | Complexidade de Espaço | Observação |
| :--- | :--- | :--- | :--- | :--- |
| `edit/tools/servidor_dev.py` | `GET [OCULTO]` (cache hit L1/L2) | $\mathcal{O}(1)$ | $\mathcal{O}(B)$ | Bundle JSON/Gzip e `ETag` em `CacheBackend` (L1 RAM Zero-Copy + L2 Redis opcional); 0 syscalls no hot-path. |
| `edit/tools/servidor_dev.py` | `POST [OCULTO]` (enfileiramento WAL sharded) | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ | Fila `SESSION_QUEUE_MAXSIZE=16384` não-bloqueante (`202 Accepted`); lotes até `512`; sharding por hash `blake2b` (`TARGETS_DB_SHARDS`). |
| `edit/tools/servidor_dev.py` | `GET [OCULTO]` (cache hit L1/L2 versionado) | $\mathcal{O}(1)$ | $\mathcal{O}(L)$ | JSON/Gzip e `ETag` (`304`) invalidados por `[OCULTO]` no `CacheBackend`; índice composto `idx_sessions_target_id_desc` no fallback SQLite. |
| `edit/tools/servidor_dev.py` | `GET [OCULTO]` (sonda LB / Auto Scaling) | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ | Fast-path lock-free com micro-TTL (`50 ms`) servindo JSON pré-serializado via `memoryview` e invalidação imediata no `close()`. |
| `edit/tools/servidor_dev.py` | Servir assets estáticos / `.br` / `.gz` | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ por req (`_ROUTE_FAST_CACHE` 32 MB) | Fast-path zero-syscall em RAM (`_ROUTE_FAST_CACHE` + `memoryview`), `ETag`/`304` + `TCP_NODELAY`. |
| `edit/src/ballistics_echarts.js` | `computeRoundSummary` em `tickChrono` (60 FPS) | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ | Métricas espaciais memoizadas; atualização incremental $\mathcal{O}(S)$ ao registrar 1 tiro. |
| `edit/src/workers/laser-worker.js` | Varredura Delta Frame + Early-Skip | $\mathcal{O}(W_{\text{roi}} \cdot H_{\text{roi}} / 4)$ | $\mathcal{O}(W_{\text{roi}} \cdot H_{\text{roi}})$ fixo | Zero-GC com ping-pong `ArrayBuffer`; pula pixels `r < 90 \|\| deltaR < 10`. |
| `edit/src/lib/aruco_universal.js` | Imagem Integral + Limiarização | $\mathcal{O}(W \cdot H)$ | $\mathcal{O}(W \cdot H)$ fixo | Buffer `_gridSampleBuf` reutilizável e cache intra-frame por `gridSize`. |

---

## 2. Arquitetura Escalável e Estratégia de Caching em Camadas

1. **Cache Híbrido L1 (RAM Zero-Copy) + L2 (Redis com Fallback Transparente) (`CacheBackend`):**
   - **L1:** `OrderedDict` LRU em RAM local servindo `memoryview` zero-copy e `_ROUTE_FAST_CACHE` com **0 syscalls `stat`/`resolve`** no hot-path (`TTL 1.0s` dev / permanente `--gzip`).
   - **L2:** Integração opcional via `REDIS_URL` ou `127.0.0.1:6379` (cliente RESP2 leve com pool `TCP_NODELAY`) para compartilhar payloads e `ETag`s entre múltiplos workers. Caso o daemon Redis não esteja ativo ou caia, um circuit-breaker alterna transparentemente para RAM local sem interrupção.
   - **Invalidação Versionada:** Commits em `SessionStore` incrementam `[OCULTO]`, invalidando automaticamente leituras e `ETag`s de `[OCULTO]` entre todos os workers.
2. **Persistência SQLite WAL Sharded de Alta Vazão (`SessionStore`):**
   - Índices compostos e de busca rápida: `idx_sessions_id_desc`, `idx_sessions_target_id_desc`, `idx_sessions_session_id` e `idx_sessions_created_at`.
   - `PRAGMA mmap_size=268435456` (256 MB), `PRAGMA temp_store=MEMORY`, `wal_autocheckpoint=1000` e `journal_size_limit=67108864` (64 MB).
   - Fila `16384`, dreno em lotes de até `512` registros e particionamento configurável via `TARGETS_DB_SHARDS` (`sessions_shard_N.db`) distribuído por hash de `target:session_id`.
3. **Motor Concorrente `1.000+` Conexões, Governança `Keep-Alive`, API Stateless e `SO_REUSEPORT`:**
   - Backlog de sockets `request_queue_size = 4096`, `DynamicWorkerPool` pré-aquecido (`min_workers = 64` no boot, teto `2048` monoprocesso e `1024`/proc no cluster) com retração ociosa (`30s`), `TCP_NODELAY`/`SO_KEEPALIVE` no `accept()` e handshake TLS paralelo (`do_handshake_on_connect=False`).
   - Governança adaptativa de `HTTP/1.1 Keep-Alive`: timeout de leitura reduzido para `1.5s` acima de `50%` de uso do pool, `KEEPALIVE_PRESSURE_RATIO = 0.75`, cabeçalho `Keep-Alive: timeout=5, max=100` e reciclagem limpa via `Connection: close` sob pressão.
   - Cache de cabeçalho HTTP `Date` a `1 Hz`, suporte multi-processo `SO_REUSEPORT` (`--processes=N` / `TARGETS_WORKERS_PROC`) e `GET [OCULTO]` lock-free em $\mathcal{O}(1)$ com micro-TTL (`50 ms`).
4. **HTTP Conditional Caching (`ETag` + `304`) & Edge Zero-GC:**
   - `Cache-Control: no-cache, must-revalidate` em rotas dinâmicas revalidáveis e código-fonte; `public, max-age=86400, immutable` em imagens de alvos; buffers ping-pong e memoização no front-end.

---

## 3. Teste Escalatório e Endurance com Locust (`test/locustfile.py` e `test/test_locust_backend.py`)

O perfil `TargetsVirtuUser(FastHttpUser)` (`geventhttpclient` em C) com `EscalatoryLoadShape` avalia a escalabilidade real do cluster HTTPS (`SO_REUSEPORT` + `4` shards SQLite WAL + ping `/`, `[OCULTO]`, Brotli/Gzip, `ETag`/`304` e `POST`/`GET [OCULTO]`) em 4 degraus (`100 -> 250 -> 500 -> 1000` usuários simultâneos, taxa de spawn padrão `100 usuários/s`), sustentando por padrão **1 hora (`3600s`) com 1.000 usuários** (ou `24s` em modo CI via `LOCUST_STAGE_DURATION=6.0`):

| Estágio | Usuários Concorrentes | Throughput (req/s) | Mediana ($P_{50}$) | Percentil $P_{95}$ | Taxa de Falha |
| :---: | :---: | :---: | :---: | :---: | :---: |
| **Estágio 1** | `100` | `497.18 req/s` | `3.0 ms` | `45.0 ms` | `0.00%` |
| **Estágio 2** | `250` | `967.34 req/s` | `2.0 ms` | `62.0 ms` | `0.00%` |
| **Estágio 3** | `500` | `1801.24 req/s` | `6.0 ms` | `100.0 ms` | `0.00%` |
| **Estágio 4** | `1000` | `1750.18 req/s` | `30.0 ms` | `580.0 ms` | `0.00%` |

- **Comparativo Antes vs. Depois (`Regras/09_plano_mitigacao_p95_e_saturacao_1000_usuarios.md`, Janela CI `24s`):**
  - **Antes (baseline saturado em 500–1.000u):** `14.062` reqs | `643.01 req/s` | $P_{50} = 24\text{ ms}$ | $P_{95} = 1.000\text{ ms}$ | $P_{99} = 2.100\text{ ms}$ (`GET [OCULTO]` $P_{95} = 2.500\text{ ms}$, média `540.08 ms`).
  - **Depois (Cluster 6 Processos `SO_REUSEPORT` x 4 Shards WAL + Pool `geventhttpclient` compartilhado):**
    - Total de requisições: **`32.464`** | Throughput médio: **`1330.92 req/s`** (pico de estágio **`1801.24 req/s`** em 500 usuários e **`1750.18 req/s`** em 1.000 usuários).
    - Latência $P_{50}$ global: **`30.0 ms`** (rotas Keep-Alive `/` e `304 ETag` em **`3–4 ms`**, `POST [OCULTO]` em **`4 ms`**, `GET [OCULTO]` em **`12 ms`**).
    - Latência $P_{95}$ global: **`580.0 ms`** | $P_{99}$ global: **`960.0 ms`** (`GET [OCULTO]` $P_{95}$ reduzido de `2.500 ms` para **`710 ms`**, média **`123.54 ms`**).
    - Taxa de erro: **`0.00%`** (`0` falhas, drenagem integral nos 4 shards SQLite WAL).

### Como Executar

1. **Suíte Automatizada Completa (31 Testes + Locust 1.000 Usuários + Gráfico PNG + JSON/CSV):**
   ```bash
   .venv/bin/python3 -m unittest discover -s test -v
   ```
2. **Execução Direta do Teste Escalatório Locust (100 -> 1.000 Usuários):**
   ```bash
   .venv/bin/python3 test/test_locust_backend.py -v
   ```
3. **Execução em Produção / Endurance 1h com Cluster `SO_REUSEPORT` e Sharding:**
   ```bash
   # Terminal 1: Iniciar cluster HTTPS (4 processos SO_REUSEPORT + 4 shards SQLite WAL)
   TARGETS_DB_SHARDS=4 .venv/bin/python3 edit/tools/servidor_dev.py --gzip --quiet --processes=4
   # Terminal 2: Iniciar Endurance 1h (100 -> 250 -> 500 -> 1000 usuários a 100/s por 3600s)
   .venv/bin/locust -f test/locustfile.py --processes 4 --headless --host https://127.0.0.1:8443
   ```
