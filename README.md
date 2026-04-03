# Newsletter Summarizer and Sender to Kindle

Busca automaticamente newsletters do email, extrai os artigos vinculados, traduz tudo para português e envia um resumo HTML otimizado para o Kindle — todos os dias via GitHub Actions.

## O que faz

1. Conecta no email via IMAP e baixa newsletters não lidas do email.
2. Extrai o texto de cada newsletter e coleta os links internos para artigos completos.
3. Faz scraping dos artigos com anti-bot bypass (stealth-requests + curl_cffi).
4. Usa Google Gemini em 3 fases: categoriza, faz merge de artigos relacionados e traduz para português.
5. Gera um HTML otimizado para leitura no Kindle (fonte Georgia, sumário interativo com âncoras, separação por relevância).
6. Envia por email para o Kindle e limpa as newsletters processadas.

## Arquitetura do Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│  Fase 0 — Cache Local (opcional)                                │
│  Se existe cache JSON do dia, pula IMAP + scraping.             │
└────────────────────────┬────────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Fase 1 — Extração de Conteúdo                                  │
│  IMAP → HTML → Markdown + coleta de links → Scraping async     │
│  stealth-requests (primary) → curl_cffi (fallback)             │
│  Burst de 3 requests, jittered pause (5-12s), retry 30s.       │
└────────────────────────┬────────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Fase 2 — Roteamento (Gemini 3.1 Flash-Lite)                   │
│  Recebe o cache de documentos, agrupa por tema e classifica:    │
│  "principal" (máx 2), "secundaria", "notas_curtas".            │
│  Output ultra-leve (~300 tokens) — só IDs e títulos.           │
└────────────────────────┬────────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Fase 2.5 — Matcher Python (local, sem API)                    │
│  Mapeia os cache_ids do roteador de volta para os textos       │
│  armazenados localmente. Cada ID é usado no máximo uma vez.    │
└────────────────────────┬────────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Fase 3 — Tradução + HTML (Gemini 3 Flash / 2.5 Flash)        │
│  Traduz cada grupo de artigos para português com HTML           │
│  estruturado. Splitting recursivo se exceder 180k tokens.      │
│  Notas curtas usam modelo mais leve (2.5 Flash).               │
└────────────────────────┬────────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Fase 4 — Entrega + Limpeza                                     │
│  Envia HTML por SMTP para o Kindle. Move emails processados    │
│  para a lixeira via IMAP. Limpa cache local.                   │
└─────────────────────────────────────────────────────────────────┘
```

### Modelos Gemini utilizados

| Modelo | Fase | Uso | Rate Limit (free tier) |
|---|---|---|---|
| `gemini-3.1-flash-lite-preview` | 2 — Router | Categorização rápida, output ~300 tokens | 15 RPM, 250k TPM |
| `gemini-3-flash-preview` | 3 — Generator | Tradução completa (artigos principais/secundários) | 5 RPM, 250k TPM |
| `gemini-2.5-flash` | 3 — Short Notes | Tradução de notas curtas + fallback do Generator | 5 RPM, 250k TPM |

### Scraper — cascade de fallback

```
stealth-requests          curl_cffi AsyncSession
(UA rotation + TLS)   →   (Chrome 145/146 TLS fingerprint)
     primary                   fallback
```

- **stealth-requests** (`>=2.0.0`): Wrapper sobre curl_cffi com rotação nativa de User-Agent (58 variantes Chrome 136). TLS fingerprint Chrome. Suporta proxy via `proxies={}`. Scraper primário para todas as URLs.
- **curl_cffi** (`>=0.15.0`): Fingerprints HTTP/3 Chrome 145/146. Sessão async nativa (sem threads). Suporta proxy residencial. Usado como fallback quando stealth-requests retorna 403/429/503 ou resposta muito pequena.
- Se ambos falham, o artigo é descartado e o pipeline continua com os demais.

## Estrutura do projeto

```
news2kindle/
├── main.py                    # Entry point (thin wrapper)
├── core/                      # Pacote principal
│   ├── __init__.py            # Re-exports
│   ├── config.py              # Constantes, tipos, helpers
│   ├── prompts.py             # Prompts Gemini (categorização + tradução)
│   ├── scraper.py             # HTTP scraping, anti-bot
│   ├── extractor.py           # HTML parsing, split sections
│   ├── gemini.py              # Google GenAI integration
│   ├── email_client.py        # IMAP/SMTP
│   └── pipeline.py            # Orquestração do pipeline
├── requirements.txt           # Dependências Python
├── .github/workflows/
│   └── daily_summary.yml      # GitHub Actions (cron diário 13:20 UTC)
├── tests/
│   ├── conftest.py            # Fixtures (newsletters .eml, mocks)
│   ├── test_scraping.py       # Scraping, anti-bot, timeout, proxy
│   ├── test_text_processing.py # HTML→MD, split, noise removal
│   ├── test_helpers.py        # Utilitários, junk detection
│   ├── test_pipeline.py       # Fluxo Fase 2→2.5→3
│   └── test_email_and_orchestrator.py  # IMAP, SMTP, orquestração
├── docs/
│   ├── anotacoes.md           # Notas técnicas e análise de scraping
│   └── prompts.md             # Documentação de prompts
├── LICENSE                    # MIT
└── README.md
```

## Configuração

### 1. Gerar credenciais

#### Email (App Password)
O script usa IMAP/SMTP. Você não pode usar sua senha normal.
- **Gmail**: Conta Google → Segurança → Verificação em 2 etapas → Senhas de App → criar uma para "Email".
- **Outros provedores**: procure "App Password" ou "Senha de aplicativo" nas configurações de segurança.

#### Google AI Studio (API Key)
1. Acesse [Google AI Studio](https://aistudio.google.com/).
2. Clique em **Get API Key** → criar chave.
3. O free tier do Gemini é suficiente para este projeto.

#### Kindle (email aprovado)
1. Amazon → Gerencie seu conteúdo e dispositivos → Preferências → Documentos pessoais.
2. Adicione seu email na lista de **e-mails aprovados**.
3. Anote o email do Kindle (`seu_nome@kindle.com`).

### 2. Configurar GitHub Secrets

No repositório: **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.

| Secret | O que colar | Exemplo |
|---|---|---|
| `EMAIL_ACCOUNT` | Seu email completo | `joao@gmail.com` |
| `EMAIL_PASSWORD` | App Password (sem aspas) | `abcd efgh ijkl mnop` |
| `IMAP_SERVER` | Servidor IMAP do provedor | `imap.gmail.com` |
| `SMTP_SERVER` | Servidor SMTP do provedor | `smtp.gmail.com` |
| `SMTP_PORT` | Porta SMTP | `587` |
| `KINDLE_EMAIL` | Email do Kindle | `joao_123@kindle.com` |
| `GOOGLE_API_KEY` | Chave do Google AI Studio | `AIzaSyD...` |
| `SCRAPER_PROXY_LIST` | *(Opcional)* Lista de proxies (CSV) | `http://user:pass@host1:port1,http://user:pass@host2:port2` |

#### Servidores por provedor de email

| Provedor | IMAP | SMTP | Porta |
|---|---|---|---|
| Gmail | `imap.gmail.com` | `smtp.gmail.com` | `587` |
| Outlook/Hotmail | `outlook.office365.com` | `smtp-mail.outlook.com` | `587` |
| Yahoo | `imap.mail.yahoo.com` | `smtp.mail.yahoo.com` | `587` |
| iCloud | `imap.mail.me.com` | `smtp.mail.me.com` | `587` |

> ProtonMail requer [Proton Mail Bridge](https://proton.me/mail/bridge) para acesso IMAP/SMTP.

### 3. Proxy residencial (opcional)

GitHub Actions roda em IPs de datacenter Azure, que a Cloudflare trata com mais rigor. Se você estiver vendo muitos 403 no scraping, configure proxies residenciais:

1. Adicione o secret `SCRAPER_PROXY_LIST` no GitHub.
2. Formato CSV: `http://user:pass@host1:port1,http://user:pass@host2:port2`
3. Os proxies são rotacionados automaticamente entre as requisições.
4. Para ~50 requests/dia, o consumo é <1GB/mês.

O script funciona normalmente sem proxy (conexão direta).

### 4. Testar o workflow

1. Vá na aba **Actions** do repositório.
2. Selecione **Daily Newsletter Summarizer**.
3. Clique em **Run workflow** → **Run workflow**.

O cron está configurado para `13:20 UTC` (10:20 BRT) todos os dias. Edite `.github/workflows/daily_summary.yml` para mudar o horário.

## Rodando localmente

```bash
# Clonar
git clone <url-do-repositorio>
cd news2kindle

# Pré-requisitos
python3 --version   # requer Python >= 3.13
pip3 --version      # requer pip instalado e atualizado

# Se necessário, instale Python e pip:
# Ubuntu/Debian:
sudo apt update && sudo apt install -y python3 python3-pip python3-venv
# macOS:
brew install python

# Ambiente virtual
python -m venv .venv
source .venv/bin/activate  # Linux/Mac

# Dependências
pip install -r requirements.txt

# Rodar
python main.py

# Testes
python -m pytest tests/ -v
```

## Dependências principais

| Biblioteca | Uso |
|---|---|
| `stealth-requests` (>=2.0.0) | Scraper primário — UA rotation + TLS Chrome (wrapper curl_cffi) |
| `curl_cffi` (>=0.15.0) | HTTP com impersonação TLS Chrome (scraper fallback) |
| `trafilatura` (>=2.0.0) | Extração de conteúdo de artigos web |
| `beautifulsoup4` + `lxml` | Parsing HTML de newsletters |
| `markdownify` | Conversão HTML → Markdown |
| `google-genai` | API Gemini (categorização + tradução) |
| `imap-tools` | Conexão IMAP para leitura de emails |
| `aiosmtplib` | Envio async de email via SMTP |
| `aiolimiter` | Rate limiter async (RPM/TPM) |
| `tenacity` | Retry com backoff exponencial |

## Variáveis de ambiente

| Variável | Obrigatória | Padrão | Descrição |
|---|---|---|---|
| `EMAIL_ACCOUNT` | Sim | — | Conta de email |
| `EMAIL_PASSWORD` | Sim | — | App Password |
| `IMAP_SERVER` | Não | `imap.gmail.com` | Servidor IMAP |
| `SMTP_SERVER` | Não | `smtp.gmail.com` | Servidor SMTP |
| `SMTP_PORT` | Não | `587` | Porta SMTP |
| `KINDLE_EMAIL` | Sim | — | Email do Kindle |
| `GOOGLE_API_KEY` | Sim | — | Chave API Google Gemini |
| `SCRAPER_PROXY_LIST` | Não | — | Lista de proxies residenciais (CSV) |

## Testes

165 testes cobrindo:

- **Scraping**: status codes, fallback cascade, timeout, detecção de captcha, proxy config
- **Extração de texto**: newsletters reais (.eml), remoção de ruído, coleta de links, filtros de domínio
- **HTML → Markdown**: preloaded data extraction, StoryBodyCompanionColumn, trafilatura
- **Pipeline**: roteamento Gemini, matcher Python, geração HTML
- **Email/Orquestração**: envio Kindle, cleanup IMAP, cache local, orquestração completa

```bash
# Rodar testes
python -m pytest tests/ -v

# Rodar testes com coverage
python -m pytest tests/ -v --cov --cov-report=html

# Abrir relatório de coverage
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

> O projeto usa `asyncio_mode = "auto"` no `pyproject.toml`, então o marker
> `@pytest.mark.asyncio` é opcional em novos testes async.

## Licença

MIT — veja [LICENSE](LICENSE).
