# Diretrizese Fluxo de Trabalho do Projeto (TARGETS_VIRTU)

Este documento define a organização de diretórios, a convenção de backups, a compressão de arquivos e as regras fundamentais de desenvolvimento do sistema de treino Dry-Fire (Laser Target).

---

## 1. Estrutura de Diretórios e Responsabilidades

```text
TARGETS_VIRTU/
├── Regras/             # Documentação, regras, especificações e histórico de modificações (.md)
│   ├── guia_de_desenvolvimento_mantis_clone.md # Guia conceitual e matemático do projeto
│   ├── 00_diretrizes_e_fluxo.md               # Este documento (normas e processos)
│   ├── 01_roteiro_e_objetivos.md              # Objetivos, passos de aprendizado e marcos
│   ├── 02_funcao_test.md                      # Regras e responsabilidades da pasta test/
│   ├── 03_funcao_lib.md                       # Regras e responsabilidades da pasta lib/
│   ├── 04_arquitetura_backend_e_camera.md     # Definição do backend, acesso à câmera e leitura de QR code
│   └── historico_alteracoes.md               # Log obrigatório de cada modificação
├── edit/               # Ambiente ativo de desenvolvimento (código-fonte editável)
│   ├── src/            # Código-fonte principal (HTML, CSS, JS/TS, assets descompactados)
│   └── tools/          # Scripts utilitários de apoio (backup, compressão gz, geradores)
├── test/               # Testes automatizados (unitários, integração e validação matemática)
├── backup/             # Preservação e contingência
│   └── src/            # Cópia fiel da última versão estável de 'edit/src/' (não editar aqui)
└── data/               # Arquivos de produção comprimidos em .gz (prontos para servidor/domínio/LittleFS)
```

---

## 2. Regras Fundamentais de Operação

### Regra 1: Onde Editar Código
* **Somente** os arquivos localizados em `edit/` (especialmente `edit/src/`) devem ser criados e modificados.
* O diretório `backup/` **nunca** deve ser editado diretamente. Ele serve como espelho de segurança da última versão antes de modificações que possam quebrar a aplicação.
* O diretório `data/` recebe apenas arquivos gerados/comprimidos (`.gz`).

### Regra 2: Registro e Documentação em `Regras/`
* Toda nova funcionalidade, alteração de arquitetura, adição de regra técnica ou mudança de diretrizes **deve** ser registrada em arquivos `.md` na pasta `Regras/`.
* Cada modificação relevante no código deve receber uma entrada no arquivo `Regras/historico_alteracoes.md` descrevendo:
  - Data e hora;
  - O que foi alterado;
  - Motivo da alteração;
  - Como testar.

### Regra 3: Fluxo de Backup e Publicação (`data/`)
Antes de iniciar uma alteração crítica ou ao concluir uma etapa funcional:
1. **Sincronizar Backup:** Copiar o conteúdo funcional de `edit/src/` para `backup/src/`.
2. **Gerar Versão de Produção (`data/`):** Comprimir os arquivos de `edit/src/` para o formato `.gz` dentro de `data/`.
3. Para facilitar, o utilitário `edit/tools/gerenciador_projeto.py` automatiza esses passos.

---

## 3. Filosofia de Aprendizado e Desenvolvimento em Parceria
* O projeto será construído de forma **modular e incremental**, explicando cada conceito novo (visão computacional no navegador, marcadores ArUco, homografia, máscaras de cor HSV e temporização de tiro).
* Testes locais rápidos no computador via webcam em primeiro lugar.
* Arquitetura web-first (HTML5/Canvas/JS/WebAssembly), garantindo compatibilidade para:
  - Teste local imediato no navegador do PC;
  - Publicação futura em um domínio web (HTTPS);
  - Acesso direto via navegador mobile ou empacotamento offline para Android (PWA / APK).

