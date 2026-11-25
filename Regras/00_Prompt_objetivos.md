Atue como um Engenheiro de Software Sênior especialista em Python, redes, protocolos HTTP/HTTPS e sistemas de baixa latência.

### Tarefa e Objetivos Principais
Refatore o ecossistema de servidor e build do projeto (`edit/tools/servidor_dev.py`, `edit/tools/gerenciador_projeto.py` e limpeza de resíduos em `edit/src/`) para otimizar a **latência na rede local Wi-Fi, concorrência, persistência assíncrona e uso de memória**, além de **remover completamente qualquer código ou asset de QR Code**, mantendo 100% da calibração e leitura baseadas exclusivamente em marcadores **ArUco**.

---

### Contexto Técnico
- **Arquitetura Edge Computing:** O processamento de visão computacional (leitura de marcadores ArUco com homografia e detecção de pulso de laser) roda 100% no cliente via Web Workers. O back-end atua apenas como servidor HTTP/HTTPS ultraleve de assets e APIs locais.
- **Stack Atual:** `http.server.ThreadingHTTPServer` + `SimpleHTTPRequestHandler`, HTTP/1.1 com `Keep-Alive` e `TCP_NODELAY`, HTTPS com certificado OpenSSL dinâmico (SAN), cache LRU em RAM para assets estáticos (`lib/Mantis-Target-Analysis-main/images/`) e `GET [OCULTO]`, suporte a `ETag`/`304`, e entrega de `.gz` em `data/`.
- **Restrição:** Zero frameworks web (apenas biblioteca padrão do Python 3). Bibliotecas como `opencv-python`, `numpy` e `Pillow` existem no `.venv` apenas para testes/validações e não devem onerar o servidor HTTP.

---

### Plano de Edição e Regras de Refatoração

#### 1. Remoção Completa de QR Code (Manter Apenas ArUco) + Melhoria A (Pipeline & Front-end)
- **No Back-end (`edit/tools/servidor_dev.py`):**
  - Exclua funções (`atualizar_qrcode_conexao`), rotas, imports (`qrcode`, `PIL` usados para QR, etc.), argumentos CLI e renderizações no terminal ou PNG relacionadas à geração de QR Code.
  - No startup do servidor, exiba apenas as URLs locais (`https://localhost:<PORTA>` e `https://<IP_LOCAL>:<PORTA>`) de forma limpa no terminal.
- **Melhoria A.1 — Limpeza de Resíduos no Front-end e Assets (`edit/src/` e réplicas):**
  - Exclua o arquivo órfão `edit/src/qrcode_conexao.png` (e garanta que sua remoção reflita em `backup/src/`, `android_app/www/` e `data/`).
  - Remova a seção morta `// 9. Leitura de QR Code Auxiliar` (`BarcodeDetector` / `detectQRCodeCalibration`) e o estado `qrCodeLastCheckTime` em `edit/src/app.js`.
  - Preserve intactas todas as rotas, dicionários, metadados e assets necessários para a detecção de marcadores **ArUco**.
- **Melhoria A.2 — Alinhamento do Pipeline de Build (`edit/tools/gerenciador_projeto.py`):**
  - Atualize a rotina de compressão em `gerenciador_projeto.py` para gerar arquivos `.br` (Brotli, caso o módulo `brotli` esteja disponível no ambiente) além dos `.gz`, garantindo paridade real entre o pipeline de build em `data/` e o servidor HTTP.

#### 2. Concorrência, Sockets e Compatibilidade + Melhoria C (Testes & TLS)
- **Pool de Threads Limitado:**
  - Substitua a criação irrestrita de threads por requisição do `ThreadingHTTPServer` por um pool controlado (`concurrent.futures.ThreadPoolExecutor`, ex: `max_workers=32`).
- **Timeouts e DNS:**
  - Mantenha `socket.settimeout(12)` nas conexões `Keep-Alive` para evitar threads travadas quando celulares perderem sinal Wi-Fi.
  - Sobrescreva `address_string()` no handler para retornar `self.client_address[0]` diretamente, eliminando qualquer bloqueio de *reverse DNS lookup*.
- **Melhoria C — Compatibilidade com a Suíte de Testes e Otimização TLS:**
  - Preserve o nome e a interface pública da classe `ThreadedHTTPSServer` e `DevRequestHandler` (sobrescrevendo `process_request` para submeter ao `ThreadPoolExecutor` e encerrando o pool com segurança em `server_close()`), garantindo 100% de compatibilidade com `test/test_backend_and_cadence.py` e `test/test_performance_backend.py`.
  - Otimize o `ssl.SSLContext` com `ssl.OP_NO_COMPRESSION` e reuso de sessão TLS (Session Tickets / cache de sessão) para reduzir a latência de reconexão HTTPS no Wi-Fi móvel.

#### 3. Cache em RAM e Entrega de Assets (`Zero-Copy` / `memoryview`)
- Suporte arquivos pré-comprimidos `.br` (Brotli) com prioridade sobre `.gz` quando o header `Accept-Encoding` aceitar `br`.
- Utilize `memoryview` sobre os buffers em `bytes` armazenados no cache LRU (`_STATIC_MEM_CACHE`) para evitar cópias extras em memória durante o envio (`wfile.write()`).
- Ajuste o cabeçalho `Cache-Control` de forma inteligente:
  - `public, max-age=86400, immutable` para imagens estáticas de alvos (`/mantis_images/`), evitando requisições `304 Not Modified` repetidas durante a sessão.
  - `no-cache, must-revalidate` com `ETag` rápido para código-fonte e JSON (`index.html`, `app.js`, `style.css`, `[OCULTO]`).

#### 4. Isolamento e Startup Rápido
- Garanta ausência total de imports globais pesados (`cv2`, `numpy`, `PIL`, `qrcode`) no servidor HTTP.
- Mantenha a geração do certificado TLS/SSL estritamente no *startup* (reaproveitando o cache em `.cert/` quando o IP local não mudou), sem jamais bloquear a thread de requisições.

#### 5. Persistência Assíncrona Blindada (`[OCULTO]`) + Melhoria B (Backpressure, WAL & Graceful Drain)
- **Fila Não-Bloqueante com Backpressure (Melhoria B.1):**
  - Implemente `POST [OCULTO]` usando `queue.Queue(maxsize=256)` e uma *worker thread* dedicada em *background*.
  - Valide `Content-Length` (máximo `65536` bytes / 64 KB) antes da leitura do socket: retorne `413 Payload Too Large` se exceder, `400 Bad Request` se o JSON for inválido, `503 Service Unavailable` se a fila estiver cheia (`queue.Full`), e `202 Accepted` imediatamente após enfileirar em $\mathcal{O}(1)$.
- **SQLite em Modo WAL e Graceful Drain (Melhoria B.2):**
  - Grave as sessões em SQLite (`.cert/sessions.db` ou `data/sessions.db` fora do espelhamento destrutivo) configurando `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=3000;`.
  - Registre drenagem limpa (*graceful shutdown* via sentinela na fila ao chamar `server_close()` / `atexit`) para garantir que nenhum treino na fila seja perdido ao encerrar o servidor.
- **Endpoint de Consulta Rápida (Melhoria B.3):**
  - Implemente `GET [OCULTO]` (com suporte a `?limit=50`) retornando em JSON as últimas sessões persistidas no SQLite para inspeção e validação automatizada nos testes.

---

### Saída Esperada na Execução do Plano
1. **Resumo das Mudanças:** Lista objetiva das melhorias aplicadas, gargalos eliminados e confirmação das rotinas e arquivos de QR Code removidos.
2. **Código Refatorado e Sincronizado:**
   - `edit/tools/servidor_dev.py` refatorado, tipado e compatível com toda a suíte de testes.
   - `edit/tools/gerenciador_projeto.py` atualizado e executado (`all`) para sincronizar `backup/src/`, `data/` e `android_app/www/` sem `qrcode_conexao.png`.
   - `edit/src/app.js` limpo de referências a `BarcodeDetector` / QR Code.
3. **Checklist e Validação Automatizada:**
   - Execução de `.venv/bin/python3 -m unittest discover -s test -v` com 100% dos testes passando (incluindo teste de `POST [OCULTO]` e `GET [OCULTO]`).
   - Registro da entrega em `Regras/historico_alteracoes.md`.
