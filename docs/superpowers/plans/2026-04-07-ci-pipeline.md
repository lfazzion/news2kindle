# CI Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GitHub Actions CI workflow that runs lint and tests on every push and pull request.

**Architecture:** Single workflow file with one sequential job — lint (ruff check + ruff format --check) then tests (pytest). No secrets required; conftest.py injects dummy env vars.

**Tech Stack:** GitHub Actions, Python 3.13, ruff, pytest

---

### Task 1: Create CI workflow file

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Create the workflow file**

```yaml
name: CI

on:
  push:
  pull_request:

jobs:
  ci:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.13'
          cache: 'pip'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Lint
        run: ruff check .

      - name: Check formatting
        run: ruff format --check .

      - name: Test
        run: python -m pytest tests/ -v
```

- [ ] **Step 2: Verify the file is valid YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo OK`
Expected: `OK`

- [ ] **Step 3: Verify lint passes locally**

Run: `ruff check . && ruff format --check .`
Expected: no output (exit 0)

- [ ] **Step 4: Verify tests pass locally**

Run: `python -m pytest tests/ -v`
Expected: all tests pass, exit 0

- [ ] **Step 5: Commit all pending changes**

Run:
```bash
git add .github/workflows/ci.yml docs/superpowers/specs/2026-04-07-ci-design.md docs/superpowers/plans/2026-04-07-ci-pipeline.md core/prompts.py
git status
git commit -m "ci: add CI workflow with lint and tests"
```
Expected: commit created with the new workflow and any other pending changes.
