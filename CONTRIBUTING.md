# Contributing

Thanks for helping improve **wbi-fleet-upgrade**.

## Quick start

### Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -U pip
```

### Install dependencies

This repo supports two common workflows:

**Option A (recommended if you’re editing the package):**

```bash
pip install -e .
pip install -e ".[dev]"
```

**Option B (requirements files):**

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### Run tests

```bash
python3 -m pytest
```

## Security best practices (please follow)

This project interacts with Google Cloud resources. Small changes can have big impact. When contributing, please follow these guidelines.

### 1) Never commit secrets

Don’t commit any of the following:

- Service account keys (`*.json`), API keys, OAuth tokens, refresh tokens
- `application_default_credentials.json`
- `.env` files containing credentials
- Logs or reports that contain sensitive data

If you accidentally commit a secret:

1. Remove it from the repo history if possible.
2. Revoke/rotate the credential immediately.
3. Open a PR noting the rotation.

### 2) Prefer least-privilege access

- Use the minimum IAM roles needed to test.
- Avoid introducing changes that require broader permissions unless there’s a clear reason.
- Where possible, keep API calls scoped to the target project and specified zones.

### 3) Be careful with logging

- Don’t log access tokens, auth headers, credential file paths, or full request/response bodies.
- Prefer logging high-level identifiers (instance name, zone, operation name) over full payloads.
- If you add new logs, consider whether they could contain user data or internal URLs.

### 4) Dry-run should be safe

If you add new operations, ensure `--dry-run`:

- does not mutate resources
- does not start/stop instances
- does not enable/disable APIs

### 5) Validate inputs and be explicit

- Treat CLI args and environment variables as untrusted input.
- Validate project IDs, locations, and instance names.
- Avoid shell injection risks in wrapper scripts (quote variables, avoid `eval`, avoid constructing commands as strings).

### 6) Dependency hygiene

- Keep runtime deps in `requirements.txt` (or `pyproject.toml` dependencies).
- Keep dev/test tooling in `requirements-dev.txt` (or `pyproject.toml` optional `dev`).
- When updating dependencies, prefer small, reviewed bumps.

Optional local check:

```bash
python3 -m pip install -U pip
python3 -m pip install pip-audit
pip-audit
```

## Code quality

- Formatting: `black .`
- Lint: `flake8`
- Types: `mypy src`

## Project layout

- Source code: `src/`
- Tests: `tests/`
- CLI entrypoint: `main.py`
- Shell wrappers: `wb-upgrade.sh`, `wb-rollback.sh`

## Requirements files: do we need more than one?

Not strictly.

Common pattern:

- `requirements.txt` = runtime deps
- `requirements-dev.txt` = runtime deps + test/lint/typecheck tools

For packaging, `pyproject.toml` can also be the single source of truth. The key is: **pick one canonical source** and avoid duplicating dependency lists across multiple places.
