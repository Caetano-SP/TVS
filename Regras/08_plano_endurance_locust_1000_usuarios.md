# Plano Técnico de Otimização e Endurance Locust (1.000 Usuários / 1 Hora)

Este documento estabelece o plano formal e arquitetural em 5 etapas para atender às tarefas de escalabilidade, endurance e alta concorrência do back-end (conforme registrado na linha 163 do roteiro de desenvolvimento e tarefas).

---

## 1. Visão Geral e Diagnóstico de Capacidade

O sistema atual opera com um servidor HTTPS concorrente (`ThreadedHTTPSServer`) sustentando com sucesso até 100 usuários simultâneos com subexposição e persistência assíncrona SQLite WAL.

Para atingir a escala de **1.000 usuários simultâneos com taxa de spawn de 100 usuários/s durante 1 hora contínua** (`locust -f locustfile.py -u 1000 -r 100`), foram mapeados os seguintes gargalos:

1. **Inanição de Pool por Conexões Keep-Alive:** Com `HTTP/1.1 Keep-Alive`, threads dedicadas bloqueiam durante o tempo ocioso de cada cliente, esgotando o limite de workers (`128`).
2. **Cache Local vs. Cache Compartilhado:** O cache em RAM local (`_ROUTE_FAST_CACHE`) precisa de ponte para instâncias concorrentes/escalonamento horizontal.
3. **Contenção no Banco de Dados:** Apenas um índice primário descendente na tabela de sessões e concorrência concentrada em arquivo SQLite único.
4. **Vazão da Fila Assíncrona:** Fila dimensionada para 4.096 itens e batches de 128 registros exigem maior folga para picos de escrita.
5. **Acoplamento Monolítico de Processo Único:** Necessidade de arquitetura stateless compatível com Load Balancer e múltiplos workers (`SO_REUSEPORT` / cluster de processos).

---

## 2. Arquitetura Alvo

```
[Locust: 1000 Usuários / Rampa 100/s]
                  │
                  ▼
   [Load Balancer / SO_REUSEPORT]
     ┌────────────┴────────────┐
     ▼                         ▼
[Worker Stateless 1]    [Worker Stateless N]
     │                         │
     ├─────────► Cache L1/L2 ◄─┤ (RAM Zero-Copy + Redis / Fallback)
     │                         │
     ▼                         ▼
[Fila Assíncrona (Queue 16k / Batch 512)]
                  │
                  ▼
[SQLite WAL Sharded + Índices Compostos]
```

---

## 3. O Plano das 5 Etapas

### Etapa 1 — Camada de Cache Híbrida L1 (RAM Zero-Copy) + L2 (Redis com Fallback Transparente)
* **Objetivo:** Manter resposta sub-milissegundo em rotas de leitura mesmo com múltiplos workers.
* **Ações Técnicas:**
  * Implementação de adaptador `CacheBackend` em `edit/tools/servidor_dev.py`.
  * Integração opcional com Redis (`127.0.0.1:6379` ou `REDIS_URL`) para ambientes clusterizados.
  * Fallback transparente em RAM compartilhada/local sem interrupção de serviço caso o daemon do Redis não esteja ativo.
  * Armazenamento e revalidação de `ETag` para `[OCULTO]` e `[OCULTO]` invalidado por chave versionada (`[OCULTO]`).

### Etapa 2 — Índices Compostos, Filas Assíncronas de Alta Vazão e Sharding sob Demanda
* **Objetivo:** Proteger a camada de persistência contra gargalos de I/O em rajadas de 1.000 usuários.
* **Ações Técnicas:**
  * Criação de índices compostos em SQLite:
    * `CREATE INDEX IF NOT EXISTS idx_sessions_target_id_desc ON sessions(target, id DESC);`
    * `CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id);`
    * `CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at DESC);`
  * Ampliação da fila assíncrona (`SESSION_QUEUE_MAXSIZE = 16384`) e dreno em lotes de até 512 registros por transação.
  * Ajustes de performance em disco: `PRAGMA mmap_size=268435456` (256 MB) e `PRAGMA temp_store=MEMORY`.
  * Mecanismo de particionamento (Sharding) configurável via `TARGETS_DB_SHARDS` distribuindo gravações entre shards (`sessions_shard_N.db`) por hash de alvo ou sessão para eliminar contenção única de escrita sob volume elevado.

### Etapa 3 — Motor Concorrente para 1.000+ Conexões, API Stateless e Load Balancer
* **Objetivo:** Garantir capacidade de conexão para 1.000 clientes paralelos sem recusa de pacotes TLS.
* **Ações Técnicas:**
  * Elevação do backlog de sockets (`request_queue_size = 2048`) e escalonamento dinâmico de conexões concorrentes.
  * Adição de endpoint de saúde e telemetria: `GET [OCULTO]` retornando estado de fila, workers e status de cache em $\mathcal{O}(1)$ para sondas de Load Balancer e Auto Scaling.
  * Garantia de estado estritamente stateless na API HTTP (sem dados de sessão em memória do processo sem persistência no backend/cache).
  * Habilitação de múltiplos processos na mesma porta via `SO_REUSEPORT` no Linux para divisão do overhead TLS entre núcleos de CPU.

### Etapa 4 — Atualização do Locustfile para Rampa de 1.000 Usuários e Endurance de 1 Hora
* **Objetivo:** Validar empiricamente a sustentação contínua de 1 hora com métricas oficiais.
* **Ações Técnicas:**
  * Configuração no `test/locustfile.py` para a curva de carga:
    * Estágios: `100 -> 250 -> 500 -> 1000` usuários simultâneos.
    * Taxa de spawn padrão: `100 usuários/s`.
    * Duração de sustentação total: `3600 segundos` (1 hora).
  * Inclusão do endpoint de `ping` (`/` e `[OCULTO]`) mantendo as tarefas de telemetria completa de disparos.
  * Atualização de `test/test_locust_backend.py` para executar validações headless no CI/CD com tolerância estrita de zero erros (`0.00%` de falhas) e geração de relatórios e gráficos comparativos em `test/output/`.

### Etapa 5 — Documentação, Homologação e Registro de Histórico
* **Objetivo:** Rastreabilidade e conformidade com as diretrizes do projeto.
* **Ações Técnicas:**
  * Atualização de `Regras/07_performance_bigo_e_caching.md` com os novos números de SLA e arquitetura sharded.
  * Atualização da lista de tarefas em `Regras/roteiro_de_desenvolvimento_e_tarefas.md`.
  * Registro de todas as modificações no `Regras/historico_alteracoes.md`.

---

## 4. Ordem e Critérios de Execução

As etapas devem ser executadas em sequência linear:
1. Etapa 1 (Cache L1/L2)
2. Etapa 2 (Índices, Fila e Sharding)
3. Etapa 3 (Stateless, Load Balancer e Conexões)
4. Etapa 4 (Locustfile 1.000 Usuários / 1h)
5. Etapa 5 (Documentação e Registro)

*Nota: A execução prática do código será iniciada apenas mediante comando explícito do usuário.*
