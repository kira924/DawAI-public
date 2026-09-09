# DawAI

DawAI is an early-stage, Egypt-first pharmacy management SaaS. It combines a
synchronous multi-tenant FastAPI backend with a bilingual React point-of-sale
client and a shared medicine-catalog foundation.

The project currently covers authenticated tenant operations, inventory and
batch expiry, sales and returns, purchases and supplier balances, shifts and
expenses, controlled stock counts, append-only stock movements, an operational
financial subledger, dashboard reporting, catalog search, and deterministic
replenishment recommendations.

> [!IMPORTANT]
> DawAI is under active development. It has not completed deployment,
> observability, offline synchronization, clinical-data verification, or
> production certification and must not currently be used for patient-care or
> statutory accounting decisions.

## Technology

- Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, and Alembic
- PostgreSQL 18
- React 19, TypeScript, Vite, and Vitest
- `uv`, Ruff, mypy, pytest, ESLint, and GitHub Actions

## Architecture

- `src/main.py` builds the API application.
- `src/api/` contains shared API contracts and middleware.
- `src/modules/` contains the domain modules.
- `alembic/` contains the PostgreSQL migration history.
- `frontend/` contains the bilingual browser client.
- `tests/` contains unit, security, contract, and PostgreSQL integration tests.
- `API_CONTRACT.md` documents the stable HTTP contract.

## Local Development

Install the backend dependencies and create local configuration:

```powershell
uv sync --locked --all-groups
Copy-Item .env.example .env
```

Replace every placeholder in `.env`, point `DATABASE_URL` at a local development
database, and run the API:

```powershell
uv run alembic upgrade head
uv run uvicorn src.main:app --reload
```

Run the frontend in another terminal:

```powershell
Set-Location frontend
npm ci
npm run dev
```

The application is then available at `http://127.0.0.1:5173` by default.

## Quality Gates

Run the same checks enforced by CI:

```powershell
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
uv run pip-audit
Set-Location frontend
npm run lint
npm run typecheck
npm run test:coverage
npm run build
```

PostgreSQL integration tests require an isolated loopback database whose name
ends in `_test`:

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/dawai_test"
uv run pytest -m integration
```

Never run the automated test suite against a shared or production database.

## Data and Security

This repository intentionally contains no production database, credentials,
pharmacy records, patient data, or proprietary source datasets. `.env` files,
local tooling, backups, and private source material are ignored.

The catalog import code expects a separately authorized local source that is not
distributed with this repository. Imported clinical interaction text must be
treated as unverified until checked against an approved clinical source.

Please report security issues through GitHub private vulnerability reporting as
described in `SECURITY.md`. Do not open a public issue for a suspected
vulnerability.

## Project Status

This public repository is a sanitized development snapshot. The private project
repository remains the operational source of truth, so public updates may arrive
in reviewed batches rather than continuously.

## License

No open-source license has been granted yet. Copyright is reserved by the project
owner. The repository is public for review and demonstration; permission to use,
modify, redistribute, or commercially deploy the code is not granted unless a
license is added later.
