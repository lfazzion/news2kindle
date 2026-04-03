# SPEC — Configuração Centralizada do Projeto (pyproject.toml + pytest + coverage + ruff)

## Objetivo
Criar `pyproject.toml` com configurações de pytest, coverage e ruff. Adicionar dependências de teste ao `requirements.txt`. Eliminar warnings de markers desconhecidos e padronizar comandos de teste/coverage.

---

## Arquivos a Criar

| Path | Tipo | Descrição/Responsabilidade |
|------|------|----------------------------|
| `pyproject.toml` | Configuração | Arquivo central de configuração do projeto. Contém seções `[tool.pytest.ini_options]`, `[tool.coverage.run]`, `[tool.coverage.report]`, `[tool.coverage.html]` e `[tool.ruff]`. |

## Arquivos a Modificar

| Path | Descrição das Mudanças |
|------|-------------------------|
| `requirements.txt` | Adicionar 3 dependências de teste: `pytest>=9.0`, `pytest-asyncio>=1.3`, `pytest-cov>=7.1`. |
| `README.md` | Atualizar seção de testes para documentar comandos padronizados com coverage (`pytest --cov`). |

---

## Detalhamento Técnico por Arquivo

### 1. `pyproject.toml` (NOVO)

Arquivo TOML mínimo — sem seção `[build-system]` ou `[project]` pois o projeto não é empacotável. Apenas seções `[tool.*]`.

```toml
# ---------------------------------------------------------------------------
# Pytest
# ---------------------------------------------------------------------------
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

# ---------------------------------------------------------------------------
# Coverage (coverage.py + pytest-cov)
# ---------------------------------------------------------------------------
[tool.coverage.run]
source = ["core", "main.py"]
branch = true

[tool.coverage.report]
exclude_also = [
    "if __name__ == .__main__.:",
    "raise NotImplementedError",
]

[tool.coverage.html]
directory = "htmlcov"

# ---------------------------------------------------------------------------
# Ruff
# ---------------------------------------------------------------------------
[tool.ruff]
target-version = "py312"
line-length = 88

[tool.ruff.lint]
select = ["E", "F", "I", "W", "UP", "B", "SIM"]
```

**Decisões aplicadas:**
- `asyncio_mode = "auto"` — markers `@pytest.mark.asyncio` existentes (31 ocorrências) se tornam opcionais. Novos testes async não precisam do marker.
- `branch = true` — coverage mede branch coverage.
- Sem `fail_under` — sem threshold mínimo de cobertura.
- Sem seção `[tool.pytest.ini_options] markers` — nenhum marker customizado registrado.
- `source = ["core", "main.py"]` — mede o pacote `core` e o entry point. Diretório `tests/` é excluído automaticamente pelo coverage.
- Ruff: `target-version = "py312"` compatível com CI. `line-length = 88` padrão do ruff. Linters selecionados: errors, import sorting, warnings, upgrade suggestions, bugbear, simplify.

### 2. `requirements.txt` (MODIFICAR)

Adicionar ao final do arquivo, após a última dependência existente:

```
pytest>=9.0
pytest-asyncio>=1.3
pytest-cov>=7.1
```

**Versões atuais (abril 2026):** pytest 9.0.2, pytest-asyncio 1.3.0, pytest-cov 7.1.0. Pino com `>=major.minor` permite upgrades de patch automaticamente.

### 3. `README.md` (MODIFICAR)

Na seção "Testes" (linha ~242), atualizar os comandos para incluir coverage:

```markdown
## Testes

```bash
# Rodar testes
python -m pytest tests/ -v

# Rodar testes com coverage
python -m pytest tests/ -v --cov --cov-report=html

# Abrir relatório de coverage
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```
```

Adicionar nota sobre `asyncio_mode = auto`:

```markdown
> O projeto usa `asyncio_mode = "auto"` no `pyproject.toml`, então o marker
> `@pytest.mark.asyncio` é opcional em novos testes async.
```

---

## Checklist de Implementação

- [x] **Fase 1: Criar `pyproject.toml`**
  - Arquivo: `pyproject.toml` (raiz do projeto)
  - Ação: Criar o arquivo com as seções `[tool.pytest.ini_options]`, `[tool.coverage.run]`, `[tool.coverage.report]`, `[tool.coverage.html]` e `[tool.ruff]` conforme detalhamento acima.
  - Referência: Seção "Detalhamento Técnico > 1" desta spec.

- [x] **Fase 2: Adicionar dependências de teste ao `requirements.txt`**
  - Arquivo: `requirements.txt`
  - Ação: Inserir `pytest>=9.0`, `pytest-asyncio>=1.3`, `pytest-cov>=7.1` ao final do arquivo, uma por linha.
  - Referência: Seção "Detalhamento Técnico > 2" desta spec.

- [x] **Fase 3: Instalar dependências e validar pytest**
  - Ação: Rodar `pip install -r requirements.txt` e depois `python -m pytest tests/ -v` para confirmar que todos os 61+ testes passam sem warnings de marker desconhecido.
  - Validação: Zero warnings de `PytestUnknownMarkWarning`. Todos os testes passam.

- [x] **Fase 4: Validar coverage**
  - Ação: Rodar `python -m pytest tests/ -v --cov --cov-report=term-missing` e verificar que o coverage mede `core/` e `main.py`, excluindo `tests/`.
  - Validação: Output mostra colunas de cobertura por arquivo. Branch coverage ativo.

- [x] **Fase 5: Validar ruff**
  - Ação: Rodar `ruff check .` e `ruff format --check .` para verificar que a configuração do `[tool.ruff]` é lida corretamente.
  - Validação: Ruff executa sem erro de configuração. Se houver violations existentes, documentar (não corrigir — fora do escopo).

- [x] **Fase 6: Atualizar README.md**
  - Arquivo: `README.md`
  - Ação: Atualizar seção "Testes" com comandos de coverage e nota sobre `asyncio_mode`.
  - Referência: Seção "Detalhamento Técnico > 3" desta spec.

---

## Perguntas / Decisões Pendentes

1. **Ruff select rules:** As regras selecionadas (`E`, `F`, `I`, `W`, `UP`, `B`, `SIM`) podem gerar violations no código existente. Deseja que eu corrija as violations como parte desta tarefa, ou apenas configure o ruff e documente as violations existentes?
Apenas configure e documente.

2. **Ruff format:** O `[tool.ruff]` configurado habilita o linter, mas não o formatter (`ruff format`). Deseja adicionar `[tool.ruff.format]` com configurações de estilo (ex: `quote-style = "double"`)?
Sim.

3. **pyproject.toml — seção `[project]`:** Mesmo sem empacotar, adicionar uma seção mínima `[project]` com `name`, `version`, `requires-python` pode ajudar ferramentas que a esperam (ex: IDEs, dependabot). Deseja incluir?
Não.
---

## Validação e Testes

- [x] `pip install -r requirements.txt` — instala sem erro
- [x] `python -m pytest tests/ -v` — todos os testes passam, zero `PytestUnknownMarkWarning`
- [x] `python -m pytest tests/ -v --cov --cov-report=term-missing` — coverage report mostra `core/` e `main.py`
- [x] `ruff check .` — ruff lê `pyproject.toml` sem erro de configuração
- [x] `ruff format --check .` — ruff format funciona com a configuração
- [x] Edge case: rodar `pytest` sem `-v` também funciona (configuração lida de `pyproject.toml`)
- [x] Edge case: rodar de subdiretório do projeto (pytest encontra `pyproject.toml` na raiz)
