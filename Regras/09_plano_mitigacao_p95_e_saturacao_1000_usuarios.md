# Plano de Execução: Mitigação de Latência de Cauda (P95) e Otimização de Saturação (500 a 1.000 Usuários)

Este documento estabelece o plano técnico de execução, dividido em **5 etapas sequenciais**, elaborado a partir da análise empírica dos artefatos de teste em `test/output/` (`locust_step_report.json`, `locust_backend_stats.csv`, `locust_backend_stats_history.csv` e `locust_escalabilidade_backend.png`).

> **Regra de Governança:** A execução prática das modificações de código descritas neste plano ocorrerá **somente após comando explícito do usuário**, podendo ser acionada etapa por etapa ou de forma integral.

---

## 1. Diagnóstico Baseado na Análise de `test/output/`

### 1.1. Métricas Coletadas no Teste Analisado (`2026-11-24`)
* **Total de Requisições (24s):** `14.062` requisições | **Falhas:** `0` (`0,00%`)
* **Throughput Médio Global:** `643,01 req/s` (meta de SLA do teste: $\ge 1.000\text{ req/s}$)
* **Estágio 1 (100 usuários):** `423,87 req/s` | $P_{50} = 6\text{ ms}$ | $P_{95} = 41\text{ ms}$
* **Estágio 2 (250 usuários — Ponto Ótimo Atual):** `883,02 req/s` | $P_{50} = 4\text{ ms}$ | $P_{95} = 56\text{ ms}$
* **Estágio 3 (500 usuários):** `564,85 req/s` | $P_{50} = 13\text{ ms}$ | $P_{95} = 550\text{ ms}$
* **Estágio 4 (1.000 usuários):** `371,75 req/s` | $P_{50} = 24\text{ ms}$ | $P_{95} = 1.000\text{ ms}$ | $P_{99} = 2.100\text{ ms}$

### 1.2. Gargalos Identificados na Arquitetura Atual
1. **Congestionamento de Handshake e Contenção em `GET [OCULTO]` ($P_{95} = 2.500\text{ ms}$, Média $= 540,1\text{ ms}$):**
   * O endpoint `[OCULTO]` é a primeira requisição disparada por cada novo usuário no bootstrap (`on_start`) e também executada continuamente na tarefa de ping.
   * Atualmente, cada chamada a `health_snapshot()` adquire múltiplos locks globais (`_executor._lock`, 4 locks das filas dos shards WAL, `_l1_lock` e `_version_lock`) e executa `json.dumps()` síncrono, gerando contenção sob 500–1.000 usuários simultâneos.
2. **Criação Síncrona de Threads na Thread de `accept()` Durante Rajadas:**
   * O `DynamicWorkerPool` inicia com `0` threads ativas e invoca `threading.Thread(...).start()` sincronicamente dentro de `submit()` na thread principal do loop `accept()` quando ocorre a rampa de 100 novos usuários/s, atrasando a retirada de novos sockets da fila do kernel.
3. **Retenção de Threads por Conexões `Keep-Alive` Ociosas entre `500` e `1.000` Clientes:**
   * No modelo `ThreadingHTTPServer`, cada conexão `HTTP/1.1 Keep-Alive` mantém 1 worker thread bloqueada em `rfile.readline()` durante o `wait_time` do cliente (150–350 ms) com timeout fixo de `12s`. Quando a concorrência ultrapassa a capacidade por processo (`512` workers), novas conexões aguardam na fila até a liberação de threads.
4. **Custo Repetitivo de Formatação HTTP no Hot-Path (`date_time_string`):**
   * `BaseHTTPRequestHandler.send_response()` recalcula `time.gmtime()` e formata a string do cabeçalho `Date` a cada resposta HTTP (`200`, `202` e `304`).

---

## 2. Arquitetura das Otimizações Propostas

```text
[Rampa Locust: 100 -> 250 -> 500 -> 1.000 Usuários @ 100/s]
                           │
                           ▼
     [Socket TCP/TLS: Backlog 4096 + SO_REUSEPORT]
                           │
                           ▼
  [DynamicWorkerPool Pré-Aquecido (Pre-warmed min_workers)]
      ├──► Keep-Alive Adaptativo (Timeout Dinâmico sob Pressão + Header Keep-Alive)
      ├──► Fast-Path [OCULTO] Lock-Free (Snapshot em RAM com Micro-TTL 50ms)
      ├──► Cache de Cabeçalho HTTP Date (1Hz, Zero time.gmtime() por Request)
      └──► Cache L1/L2 Zero-Copy + Shards SQLite WAL Assíncronos
```

---

## 3. Plano de Execução Separado por Etapas

### Etapa 1 — Otimização do Backlog TCP, Pre-Warming do Pool de Workers e Handshake TLS
* **Arquivos-Alvo:** `edit/tools/servidor_dev.py`
* **Objetivo:** Eliminar a latência de criação de threads durante o pico de conexões e expandir a fila de aceitação do kernel para rajadas de 1.000 conexões.
* **Ações Técnicas:**
  1. Elevar `SOCKET_BACKLOG_SIZE` de `2048` para `4096` em `ThreadedHTTPSServer`.
  2. Implementar **pré-aquecimento (*pre-warming*)** no `DynamicWorkerPool.__init__()`, inicializando `min_workers` threads imediatamente na subida do processo para que a thread de `accept()` apenas enfileire sockets em $\mathcal{O}(1)$ sem bloquear em `pthread_create`.
  3. Configurar opções de socket de baixa latência (`TCP_NODELAY` e `SO_KEEPALIVE`) logo na aceitação da conexão.
* **Critério de Aceite da Etapa 1:**
  * Pool inicia com `min_workers` pré-alocados (`stats()["current"] >= min_workers` imediatamente após o boot) e todos os testes unitários de servidor passam.

---

### Etapa 2 — Governança Adaptativa de `HTTP/1.1 Keep-Alive` sob Alta Concorrência (500–1.000 Usuários)
* **Arquivos-Alvo:** `edit/tools/servidor_dev.py`
* **Objetivo:** Impedir que conexões `Keep-Alive` ociosas esgotem o pool de threads quando o número de clientes simultâneos escalar para 500 e 1.000 usuários.
* **Ações Técnicas:**
  1. Reduzir dinamicamente o timeout de leitura entre requisições subsequentes na mesma conexão `Keep-Alive` quando a utilização do `DynamicWorkerPool` ultrapassar `50%` (timeout adaptativo para reciclagem rápida de sockets ociosos).
  2. Ajustar `KEEPALIVE_PRESSURE_RATIO` (de `0.90` para `0.75`) e incluir cabeçalho explícito `Keep-Alive: timeout=5, max=100` nas respostas persistentes, sinalizando reciclagem limpa sob pressão.
  3. Dimensionar o teto padrão de workers por processo no cluster para absorver todas as conexões simultâneas sem fila de espera de thread.
* **Critério de Aceite da Etapa 2:**
  * Nenhuma conexão nova fica retida na fila do pool por inanição de `Keep-Alive` durante os estágios de 500 e 1.000 usuários.

---

### Etapa 3 — Fast-Path Lock-Free para `GET [OCULTO]` e Cache de Cabeçalho `Date` (1 Hz)
* **Arquivos-Alvo:** `edit/tools/servidor_dev.py`
* **Objetivo:** Reduzir a latência de cauda ($P_{95}$) de `GET [OCULTO]` de `2.500 ms` para `< 250 ms` (e mediana $< 10\text{ ms}$) e cortar overhead de CPU em todas as rotas HTTP.
* **Ações Técnicas:**
  1. Implementar cache de micro-TTL (`50 ms`) para os bytes JSON pré-serializados de `ThreadedHTTPSServer.health_snapshot()`, servindo o payload via `memoryview` sem adquirir os locks do pool, das filas dos shards ou do cache L1 em 99% das requisições de sonda.
  2. Invalidar imediatamente o snapshot de saúde apenas quando o servidor ou o `SessionStore` mudarem de estado (`close()`).
  3. Sobrescrever `DevRequestHandler.date_time_string()` com cache de 1 segundo (1 Hz), eliminando milhares de chamadas redundantes a `time.gmtime()` por segundo.
* **Critério de Aceite da Etapa 3:**
  * `GET [OCULTO]` responde com payload JSON válido (`status`, `stateless`, `workers`, `queue`, `cache`) em $\mathcal{O}(1)$ sem contenção de locks sob rajada concorrente.

---

### Etapa 4 — Calibração do Teste Escalatório Locust e Validação de SLA (100 → 1.000 Usuários)
* **Arquivos-Alvo:** `test/test_locust_backend.py` e `test/locustfile.py`
* **Objetivo:** Alinhar a concorrência do cluster no teste automatizado e validar o cumprimento estrito de todos os SLAs de performance até 1.000 usuários.
* **Ações Técnicas:**
  1. Ajustar a configuração de workers por processo do cluster em `test/test_locust_backend.py` para operar em conjunto com a governança `Keep-Alive` da Etapa 2.
  2. Garantir no `test/locustfile.py` o reuso eficiente do pool de conexões `geventhttpclient` entre o bootstrap e as tarefas contínuas.
  3. Validar os critérios estritos da suíte:
     * Taxa de falhas: **`0,00%`** (`total_failures == 0`);
     * Throughput médio global: **$\ge 1.000\text{ req/s}$**;
     * Mediana global ($P_{50}$): **$\le 35\text{ ms}$** ($P_{50} \le 10\text{ ms}$ no Estágio 1 e $\le 15\text{ ms}$ no Estágio 2);
     * Latência de cauda global ($P_{95}$): **$< 1.000\text{ ms}$**;
     * Drenagem integral nos 4 shards SQLite WAL.
* **Critério de Aceite da Etapa 4:**
  * Execução de `.venv/bin/python3 test/test_locust_backend.py -v` aprovada com 100% de sucesso e geração dos novos relatórios em `test/output/`.

---

### Etapa 5 — Homologação da Suíte Completa, Artefatos em `test/output/` e Documentação em `Regras/`
* **Arquivos-Alvo:** `test/output/*`, `Regras/07_performance_bigo_e_caching.md`, `Regras/roteiro_de_desenvolvimento_e_tarefas.md` e `Regras/historico_alteracoes.md`
* **Objetivo:** Consolidar as novas métricas auditadas, sincronizar os artefatos de saída e registrar o histórico técnico conforme a Regra 2 de `Regras/00_diretrizes_e_fluxo.md`.
* **Ações Técnicas:**
  1. Executar a suíte completa `.venv/bin/python3 -m unittest discover -s test -v` garantindo 100% de aprovação em todos os testes do repositório.
  2. Atualizar a tabela de resultados em `Regras/07_performance_bigo_e_caching.md` com os números reais antes/depois obtidos em `test/output/locust_step_report.json`.
  3. Marcar as etapas como concluídas em `Regras/roteiro_de_desenvolvimento_e_tarefas.md` e registrar a entrada detalhada em `Regras/historico_alteracoes.md`.
* **Critério de Aceite da Etapa 5:**
  * Todos os 29+ testes automatizados aprovados, artefatos de `test/output/` atualizados e documentação 100% sincronizada.

---

## 4. Ordem de Execução sob Comando do Usuário

| Ordem | Etapa | Escopo Principal | Status |
| :---: | :--- | :--- | :---: |
| **1** | **Etapa 1** | Backlog TCP (`4096`), Pre-warming do `DynamicWorkerPool` e Tuning de Socket/TLS | `[x] Concluída` |
| **2** | **Etapa 2** | Governança Adaptativa de `Keep-Alive` e Reciclagem Rápida sob Saturação (500–1.000u) | `[x] Concluída` |
| **3** | **Etapa 3** | Fast-Path Lock-Free (Micro-TTL) em `GET [OCULTO]` e Cache `Date` (1 Hz) | `[x] Concluída` |
| **4** | **Etapa 4** | Calibração e Validação de SLA no Locust (`>= 1.000 req/s`, `P95 < 1.000 ms`, `0%` erros) | `[x] Concluída` |
| **5** | **Etapa 5** | Suíte Completa, Atualização de `test/output/` e Registro em `Regras/` | `[x] Concluída` |
