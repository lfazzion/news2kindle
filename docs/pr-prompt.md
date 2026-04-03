## Prompt de Git Workflow (news2kindle)

Você é um engenheiro de software sênior responsável por executar o workflow Git completo
de uma fase recém-implementada. Siga EXATAMENTE as etapas abaixo, em ordem, sem pular
nenhuma. Em caso de dúvida, pergunte — nunca assuma.

---

## PRÉ-REQUISITOS (Analise as mudanças e preencha antes de submeter)

- [ ] `[BRANCH_BASE]` → branch base real (ex: `main`)
- [ ] `[NOME_DA_FASE]` → nome descritivo da fase implementada
- [ ] `[TIPO]` → tipo Conventional Commit (`feat` | `fix` | `refactor` | `chore` | `perf` | `docs`)
- [ ] `[NOME_DO_BRANCH]` → nome completo do branch (ex: `feat/add-stealth-requests`)

---

## CONTEXTO DO PROJETO

- Projeto: **news2kindle** — Pipeline Python >= 3.13, Scraping async (stealth-requests + curl_cffi fallback), Integração LLM (Gemini 3.1/2.5 API), IMAP/SMTP
- Branch base: `[BRANCH_BASE]`
- Fase implementada: `[NOME_DA_FASE]`
- Tipo de mudança: `[TIPO]`

---

## ETAPA 0 — PRÉ-VERIFICAÇÃO DO ESTADO DO REPOSITÓRIO

Antes de qualquer ação, verifique o estado:

```bash
git branch          # confirmar branch atual
git status          # confirmar ausência de mudanças não relacionadas
git diff --check    # detectar erros de whitespace (trailing spaces, mixed tabs)
```

### Output esperado:
- Informe o branch atual
- Se houver mudanças **não relacionadas** à fase: execute `git stash` e reporte antes de continuar
- Se `git diff --check` reportar erros: corrija antes de continuar (`ruff format .`)

---

## ETAPA 1 — LEITURA DE CONTEXTO (obrigatória antes de qualquer git action)

1. Leia `README.md` — verificar a arquitetura do scraping em cascade, modelos ativos da API do Gemini e estrutura do pipeline.
2. Leia `docs/anotacoes.md` e `docs/prompts.md` — verificar decisões arquiteturais em scraping, notas técnicas e restrições.
3. Identifique se a fase envolve core pipeline, novos scrapers, extractors, prompts ou integrações IMAP/SMTP — e verifique brevemente os arquivos da pasta `core/` correspondentes.

### Output esperado:
- Confirme os arquivos lidos
- Liste quaisquer decisões ratificadas ou limitações de infraestrutura (proxies, rate limits) **relevantes a esta fase**

---

## ETAPA 2 — ANÁLISE DE MUDANÇAS

Execute e analise todos os outputs:

```bash
git status
git diff --stat
git diff
git log --oneline -5
```

### Output esperado:
- Liste TODOS os arquivos: modificados (M), criados (A), deletados (D), renomeados (R)
- Identifique o escopo de cada mudança (`scraper`, `extractor`, `gemini`, `email`, `pipeline`, `test`, `config`, `prompt`, `script`)
- Detecte se há mudanças **NÃO relacionadas** à fase — se sim, NÃO inclua no mesmo commit
- Verifique se há arquivos sensíveis (`.env`, `pyproject.toml` contendo keys reais, keys API hardcoded do Gemini, credenciais IMAP) — NUNCA inclua
- **Se detectar mistura de escopos**: proponha divisão em commits atômicos ANTES de prosseguir

### Se falhar:
- Se `git diff` mostrar conflitos de merge: **PARE**, reporte e aguarde instrução
- Se houver arquivos sensíveis staged: `git restore --staged <arquivo>` imediatamente

---

## ETAPA 3 — VALIDAÇÃO PRÉ-BRANCH (Definition of Done check)

Antes de criar o branch, confirme CADA item. Se algum falhar, CORRIJA antes de prosseguir:

| # | Check | Comando / Ação |
|---|-------|----------------|
| 1 | Todos os testes passam | `python -m pytest tests/ -v` |
| 2 | Sem warnings de sintaxe ou linting | `ruff check .` |
| 3 | Sem falhas de formatação | `ruff format --check .` |
| 4 | Coverage estável | `python -m pytest tests/ -v --cov` |
| 5 | Novo código tem testes correspondentes | Verificar se os arquivos em `tests/` espelham suas alterações em `core/` ou `main.py` |
| 6 | Sem secrets hardcoded | Revisão manual de todos os arquivos modificados |

### Output esperado:
- Tabela com status ✅ / ❌ de cada item
- Para cada ❌: descreva o problema e como corrigiu (ex: rodando `ruff check --fix .` ou `ruff format .`) antes de continuar

### Se falhar:
- Se testes falharem: corrija o código, não os testes
- Se não houver test correspondente: crie antes de continuar — nunca pule esta etapa

---

## ETAPA 4 — CRIAÇÃO DO BRANCH

```bash
git checkout [BRANCH_BASE]
git pull origin [BRANCH_BASE]
git checkout -b [NOME_DO_BRANCH]
```

Convenções de nomenclatura:
- Prefixo: `feat/`, `fix/`, `refactor/`, `chore/`, `perf/`, `docs/`, `test/`, `hotfix/`
- Formato: `[tipo]/[verbo]-[descricao-curta]` em kebab-case
- Exemplos válidos: `feat/add-stealth-requests`, `fix/resolve-cloudflare-403`, `refactor/extract-html-parser`
- Exemplos INVÁLIDOS: `feature/update`, `branch1`, `changes`, `wip`

### Output esperado:
- Confirme branch criado com `git branch --show-current`

### Se falhar:
- Se branch já existir: **NÃO sobrescreva** — reporte e pergunte antes de prosseguir
- Se `git pull` falhar: reporte o erro exatamente como apareceu e aguarde instrução

---

## ETAPA 5 — STAGING INTELIGENTE

Regras:
- **NUNCA** use `git add -A` — adicione arquivos individualmente por grupo lógico
- Use `git add <arquivo>` para cada arquivo, verificando com `git status` após cada grupo
- Para arquivos com **mudanças mistas** (ex: fix + refactor no mesmo arquivo), use `git add -p <arquivo>`
  para staging seletivo por hunk — isso permite commits verdadeiramente atômicos
- Se houver múltiplos commits lógicos independentes, **DIVIDA em commits atômicos**
- Confirme staging com `git diff --cached --name-only` antes de commitar
- Se `git diff --cached --name-only` retornar vazio: **PARE** — nada está staged

### Output esperado:
- Lista de arquivos staged por grupo lógico
- Confirmação de `git diff --cached --name-only`

---

## ETAPA 6 — COMMIT (Conventional Commits)

Formato obrigatório:

```
<tipo>(<escopo>): <descrição concisa imperativa>

<corpo opcional: O QUE mudou e POR QUE — apenas se não óbvio>

<footer: Closes #N, Fixes #N, BREAKING CHANGE: ...>
```

Tipos: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `perf`, `ci`, `build`, `revert`
Escopo: componente afetado (`scraper`, `gemini`, `extractor`, `email`, `pipeline`, `config`, etc.)

Exemplos:
```
feat(scraper): add stealth-requests support with proxy fallback

Implement bypass for Datadog/Cloudflare 403s using stealth-requests 
with Chrome 136 TLS fingerprint. Added jittered pause (5-12s).

Closes #42
```

```
fix(extractor): resolve recursion error on huge html DOMs

HTML parsing was failing on nested tables. Replaced recursive node
walking with iterative trafilatura logic context.

Fixes #87
```

**Opção A — heredoc:**
```bash
git commit -F- <<'COMMIT_MSG'
feat(scraper): add stealth-requests support with proxy fallback

Implement bypass for Datadog/Cloudflare 403s using stealth-requests
with Chrome 136 TLS fingerprint. Added jittered pause (5-12s).

Closes #42
COMMIT_MSG
```

Confirme com `git log -1 --format=fuller` que o commit está correto.

### Output esperado:
- A mensagem de commit completa usada
- Output de `git log -1 --format=fuller`

---

## ETAPA 7 — PUSH

```bash
git push -u origin [NOME_DO_BRANCH]
```

### Output esperado:
- URL do branch no GitHub (reportada pelo `git push`)

### Se falhar:
- Se push falhar por divergência: **NUNCA use `--force`** — reporte o erro e aguarde instrução
- Se falhar por auth: reporte e aguarde — nunca tente contornar

---

## ETAPA 8 — PULL REQUEST

Verifique autenticação antes de criar o PR:

```bash
gh auth status
```

### Se falhar:
- Se `gh` não estiver autenticado ou não instalado: forneça o template do PR manualmente para criar via interface GitHub e continue para a ETAPA 9

Use `gh pr create` com o template estruturado abaixo. Ajuste os campos ao contexto real da fase:

```bash
gh pr create \
  --base [BRANCH_BASE] \
  --title "<título descritivo, <70 chars>" \
  --label "[TIPO]" \
  --body "$(cat <<'EOF'
## Resumo
[2-3 sentenças: O que esta fase implementa e por quê]

## Tipo de Mudança
- [ ] feat — nova funcionalidade
- [ ] fix — correção de bug
- [ ] refactor — refatoração sem mudança de comportamento
- [ ] perf — melhoria de performance
- [ ] chore — tarefa de manutenção
- [ ] docs — documentação

## O que mudou

### Arquivos Criados
| Arquivo | Propósito |
|---------|-----------|
| `core/exemplo.py` | [descrição do módulo ou feature] |
| `tests/test_exemplo.py` | [arquivos de teste do módulo adicionado] |

### Arquivos Modificados
| Arquivo | Natureza da Mudança |
|---------|---------------------|
| `core/scraper.py` | [o que mudou e por quê] |
| `tests/test_scraping.py` | [quais cenários novos foram cobertos] |

### Dependências Adicionadas (se aplicável)
- Package: `[nome]` — [motivação da lib e utilidade]

## Pontos Importantes
- [Decisão técnica 1 e motivo - exemplo: porque usamos regex e não BS4 para extrair o text]
- [Decisão técnica 2 e motivo - exemplo: porque o delay tem jitter]

## Como Testar
1. `python -m pytest tests/[seu_arquivo_criado]_test.py -v`
2. Run pipeline: `python main.py` usando `KINDLE_EMAIL`, `EMAIL_ACCOUNT` etc. 
3. [Outra verificação manual importante se aplicável]

## Checklist
- [ ] Testes de pipeline passam: `python -m pytest tests/ -v`
- [ ] Validações de código estáticas: `ruff check .` e `ruff format --check .`
- [ ] Cobertura de testes sem impacto (coverage pass)
- [ ] `docs/anotacoes.md` ou `README.md` atualizado com o pipeline flow se cabível.
- [ ] Sem hardcoded credentials (IMAP / Google Gemni / Proxies)
- [ ] Branch naming e Commit message baseadas em Conventional Commits

## Riscos / Atenção Manual
- [Alterou proxies, rate-limiter, ou impacta requisições ao provedor IMAP?]
- [Nenhum, se cobertura de testes prova estabilidade total]
- [Requer atualização dos secrets no GitHub Actions dependendo da mudança]

## Dependências
- [Novos pacotes em requirements.txt]
- [Impactos com outros scrapers na base]

## Breaking Changes
[Nenhum] ou [descritivo]

---
🤖 Generated with AI Assistant | Branch: [NOME_DO_BRANCH]
EOF
)"
```

### Output esperado:
- URL do PR criado

---

## ETAPA 9 — VERIFICAÇÃO FINAL

```bash
gh pr view          # confirmar PR criado corretamente
git status          # confirmar branch limpo, sem staged files esquecidos
git log --oneline -3
```

### Output esperado (relatório final):

1. ✅ Branch criado: `[nome]`
2. ✅ Commits: `[hash] [mensagem]`
3. ✅ PR URL: `[link]`
4. ✅ Resumo executivo (3-5 linhas)
5. ⚠️ Riscos ou itens que precisam de atenção manual

---

## ROLLBACK — Se precisar desfazer

| Situação | Comando |
|----------|---------|
| Desfazer staging de um arquivo | `git restore --staged <arquivo>` |
| Desfazer último commit (local, mantém mudanças) | `git reset --soft HEAD~1` |
| Deletar branch local (antes do merge) | `git checkout [BRANCH_BASE] && git branch -d [NOME_DO_BRANCH]` |
| Desfazer push (cria revert commit) | `git revert HEAD && git push` |
| Verificar histórico de HEADs | `git reflog` |

> **NUNCA** use `git reset --hard` em branch compartilhado — use `git revert` para branches já pusheados.

---

## REGRAS INVIOLÁVEIS

1. Nunca force-push para `main` ou `master`
2. Nunca inclua secrets, senhas de Aplicativo de e-mail, keys do Google Gemini ou credenciais no repositório.
3. Sempre rode testes (ETAPA 3) e o formator Ruff antes de qualquer commit
4. Commits atômicos > commits monolíticos — um commit por mudança lógica
5. Nunca delete branch local antes do merge do PR
6. Se algo falhar: documente o erro e PEÇA instrução — nunca adivinhe nem "conserte" sem entender
7. PR body deve ser autocontido — alguém sem contexto deve entender o quê e por quê
8. Respeite TODAS as convenções e estilo ditados pelo Ruff no pyproject.toml 
9. Se a fase gerou decisão arquitetural considerável, documente-a na `docs/anotacoes.md` e em `README.md`
10. Nunca use `git add -A` — sempre staging explícito por arquivo
11. Nunca use `--no-verify` em commits — respeite todos os git hooks
12. Nunca use `git reset --hard` em branches já pusheados — use `git revert`