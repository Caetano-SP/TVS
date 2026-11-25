# Arquitetura do Backend, Acesso à Câmera e Leitura de QR Code

Este documento define o funcionamento do **Backend**, os requisitos para acesso seguro à **Câmera do celular e PC**, e a estratégia de **leitura de QR Code** para identificação de alvos e proporção de projeções.

---

## 1. O Papel do Backend vs. Frontend (Edge Computing)

### 1.1 Por que a Visão Computacional roda no Frontend (Edge)?
* Um disparo a laser de treino (*dry-fire*) dura apenas **50 a 100 milissegundos** (cerca de 1 a 3 frames da câmera a 30/60 FPS).
* Se enviássemos o vídeo em tempo real para um servidor remoto através da rede, a latência de transmissão (50ms a 200ms) e a compressão de streaming causariam perda frequente de tiros e imprecisão crítica no *Shot Timer*.
* **Decisão Arquitetural:** O loop de captura, retificação de perspectiva e detecção luminosa do laser é executado **diretamente no dispositivo** (computador ou celular) via JavaScript, Canvas e WebAssembly/OpenCV.js.

### 1.2 Responsabilidades Essenciais do Backend
O Backend é fundamental para as seguintes funções:
1. **Servidor HTTP/HTTPS com Suporte a Compressão (`data/*.gz`):**
   - Servir os arquivos da aplicação compilados e comprimidos na pasta `data/` com o cabeçalho `Content-Encoding: gzip`.
   - Gerenciar o protocolo **HTTPS** com certificados locais válidos (ou em domínio de produção), eliminando bloqueios de segurança dos smartphones.
2. **API de Metadados e Perfis de Alvos:**
   - Fornecer as geometrias oficiais de alvos (alvos redondos concêntricos, alvos de silhueta IPSC/IDPA, alvos múltiplos, telas projetadas).
3. **Persistência de Treinos e Telemetria:**
   - Armazenar séries de tiro, tempos de reação, coordenadas $(X, Y)$ dos impactos, cálculo de agrupamento e estatísticas do atirador.
4. **Módulo de Testes e Validação Matemática (Python):**
   - Conforme definido em `Regras/02_funcao_test.md`, o ambiente Python no backend valida algoritmos, gera matrizes de teste e alvos antes de irem para o frontend.

---

## 2. Acesso à Câmera no Computador e Smartphone

### 2.1 Requisito Crítico: Contexto Seguro (HTTPS ou localhost)
* A especificação do W3C para `navigator.mediaDevices.getUserMedia` **bloqueia totalmente** o acesso à câmera em redes locais se a conexão for `http://` não seguro 
* Para testar no celular conectado à mesma rede Wi-Fi do computador:
  - O backend em desenvolvimento deve rodar com suporte a HTTPS (ex: certificado TLS autoassinado ou ferramenta como `mkcert`);
  - No computador, `http://localhost` é considerado seguro por padrão pelo navegador.
  - No domínio público final, o acesso ocorre sob HTTPS com certificado SSL padrão.

### 2.2 Seleção de Lente e Configurações de Captura
* **No Celular:** Selecionar obrigatoriamente a câmera traseira (*câmera principal de alta definição*), evitando câmeras frontais ou grande-angulares que distorçam as bordas:
  ```javascript
  const constraints = {
    video: {
      facingMode: { ideal: "environment" },
      width: { ideal: 1280 },
      height: { ideal: 720 },
      frameRate: { ideal: 60, min: 30 }
    },
    audio: false
  };
  ```
* **Controle de Exposição e Foco:**
  - Evitar que a câmera fique ajustando o foco dinamicamente durante o treino.
  - Reduzir a compensação de exposição para escurecer o fundo e destacar o ponto luminoso do laser.

---

## 3. Leitura de QR Code e Marcadores de Projeção

### 3.1 Função do QR Code no Alvo
O QR Code atua como o "RG inteligente" do alvo ou da projeção:
1. **Identificação Instantânea do Alvo:** Sem que o atirador precise selecionar menus no celular, a câmera lê o QR Code e descobre automaticamente qual alvo está sendo utilizado.
2. **Definição de Dimensões e Escala Métrica:** O QR Code codifica as dimensões físicas reais (em milímetros) ou a proporção geométrica da projeção.
3. **Mapeamento de Zonas de Pontuação:** Define se a pontuação é baseada em anéis circulares (10 a 5) ou zonas de silhueta (Alfa, Charlie, Delta).

### 3.2 Formato dos Dados do QR Code (Payload JSON Compacto)
Exemplo de dados embutidos no QR Code do alvo impresso ou projetado:
```json
{
  "id": "A4_IPSC_01",
  "tipo": "silhueta",
  "w_mm": 210,
  "h_mm": 297,
  "marcadores": [0, 1, 2, 3],
  "zonas": {
    "alfa": 5,
    "charlie": 3,
    "delta": 1
  }
}
```

### 3.3 Casos de Uso: Alvo de Papel vs. Alvo Projetado na Parede
* **Caso A: Alvo de Papel Impresso (A4):**
  - O QR Code fica posicionado em local não destrutivo da folha (ex: rodapé ou canto).
  - Os 4 marcadores ArUco nos cantos fornecem a retificação de homografia $(0, 1, 2, 3)$, e o QR Code informa o modelo do alvo.
* **Caso B: Alvo Projetado (Projetor Multimídia na Parede):**
  - Se o usuário projeta o alvo numa parede branca usando um projetor, o tamanho da projeção varia conforme a distância do projetor.
  - O QR Code projetado informa a proporção da imagem (ex: 16:9 ou 4:3) e o tamanho da grade projetada.
  - Ao detectar o QR Code e os marcadores de canto da projeção, a câmera calibra a proporção e sabe exatamente onde estão as zonas de impacto na parede.

### 3.4 Motor de Decodificação do QR Code
* **No Cliente (Navegador):**
  - Uso da API nativa moderna `window.BarcodeDetector` (altamente eficiente e acelerada por hardware no Android/Chrome).
  - Fallback via biblioteca WebAssembly/JS (ex: `jsQR` ou `ZXing`) caso o navegador não possua suporte nativo.
* A leitura do QR Code ocorre no momento da calibração (apenas uma vez ao iniciar ou quando o alvo for alterado), não consumindo processamento durante os disparos.

---

## 4. Estrutura do Backend Recomendada

O backend inicial será estruturado em Python em `edit/src/backend/` ou `edit/backend/`:
* **Servidor Leve:** Servidor HTTP/HTTPS com suporte a rotas estáticas para servir os arquivos compactados em `.gz` de `data/` ou os fontes de `edit/src/`.
* **Geração de Certificado HTTPS Local:** Script para gerar certificado SSL autoassinado para testes imediatos entre celular e PC na mesma rede Wi-Fi.
* **Endpoints da API:**
  - `GET [OCULTO]`: Lista os modelos de alvos cadastrados e seus metadados.
  - `POST [OCULTO]`: Salva os resultados de um treino (tempo, pontuação, disparos).
  - `GET [OCULTO]{id}/qrcode`: Gera a imagem do QR Code para qualquer alvo configurado.
