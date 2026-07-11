# Repository Guidelines

## Project Structure & Module Organization

This repository contains two application layers. `backend/app/` is the FastAPI service: API routes live in `api/routers/`, database models in `models/`, request schemas in `schemas/`, business logic in `services/`, and Celery jobs in `worker/`. Backend tests are in `backend/tests/`. `miniapp/` is the native WeChat client; each screen keeps its `.js`, `.json`, `.wxml`, and `.wxss` files together under `pages/`, while shared API clients and helpers belong in `services/` and `utils/`. Treat `graphify-out/` as generated analysis output rather than application source.

## Build, Test, and Development Commands

- `python3 -m venv .venv && source .venv/bin/activate` creates and activates a local environment.
- `pip install -r requirements.txt` installs backend and test dependencies.
- `uvicorn backend.app.main:app --reload` starts the API with automatic reload.
- `pytest backend/tests` runs the complete backend test suite; use `pytest backend/tests/test_v1_flow.py -q` for a focused run.
- Open `miniapp/` in WeChat DevTools to build, preview, and debug the client; its project settings are in `miniapp/project.config.json`.

## Coding Style & Naming Conventions

Use four-space indentation and PEP 8 conventions for Python. Name modules, functions, and variables with `snake_case`, and classes with `PascalCase`. Keep route handlers thin and move reusable domain behavior into `services/`. For mini-program code, follow existing two-space JavaScript/JSON indentation, use `camelCase` identifiers, and preserve the `index.*` page-file convention. No formatter or linter is configured, so match neighboring code and avoid unrelated reformatting.

## Testing Guidelines

Tests use pytest and follow `test_*.py` filenames with `test_*` functions. Add tests beside the closest existing flow or service test. Cover successful behavior and configuration/error paths. There is no enforced coverage threshold; regressions should still include a focused test.

## Commit & Pull Request Guidelines

History uses brief, imperative summaries, often in Chinese (for example, `功能修正`). Keep each commit focused and make the subject specific enough to identify the change. Pull requests should explain the problem and solution, list verification commands, link relevant issues, and include screenshots or recordings for mini-program UI changes. Call out database, environment-variable, or API-contract changes explicitly.

## Security & Configuration

Keep secrets in an untracked `.env`; never commit API keys or production credentials. Local development defaults to `backend/dev.db`. Use `DATABASE_URL` for deployed databases and document any newly required environment variables in `README.md`.
