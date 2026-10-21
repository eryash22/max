# MAX Biomedical — Hosting-Ready Source Bundle

This folder is a **clean copy of the application source only**, prepared from the
working project at `D:\Codex\2026-08-17\read-master-codex-prompt-md-and` (including
its latest uncommitted work — not just `git HEAD`) so it can be uploaded to a new
GitHub repository or handed to a hosting platform without the ~1.1 GB of local
development dependencies, caches and machine-specific files that live alongside it
in the original project.

**This copy contains no secrets, no database data, and no uploaded business
documents.** See "What's excluded" below for exactly why, and what you still need
to configure or transfer separately.

## What's included

```
hosting/
├── README.md                    — this file
├── DEPLOYMENT_CHECKLIST.md      — steps to take before going live
├── .gitignore                   — ready for a fresh git repo
├── backend/
│   ├── app/                     — FastAPI source (routers, services, models, schemas, tests)
│   ├── migrations/              — Alembic migration history (0001 → 0027)
│   ├── scripts/create_admin.py  — first-admin bootstrap script
│   ├── requirements.txt         — pinned Python dependencies
│   ├── alembic.ini, pytest.ini  — configuration
│   └── .env.example             — sanitized environment variable template
└── frontend/
    ├── app/, components/, hooks/, lib/, types/, styles/, services/
    │                             — Next.js (App Router) source
    ├── package.json, package-lock.json
    ├── next.config.mjs, tsconfig.json, next-env.d.ts
    └── .env.example             — sanitized environment variable template
```

Everything the application actually imports at runtime is here: the full router/
service/model/schema tree, every Alembic migration, the Phase 1/Phase 2
communication engine, the Device Catalog / Asset / Allocation / QR system, RBAC,
dashboard, and the backend's test suite (`backend/app/tests/`, 79 test files) as a
regression guard for anyone continuing this work.

## What's excluded, and why

| Excluded | Why |
|---|---|
| `backend/.venv/`, root `.venv/`, `backend/.venv.broken-20261017/` | Local Python virtualenvs — regenerate with `pip install -r backend/requirements.txt` |
| `frontend/node_modules/` | Regenerate with `npm install` from `package-lock.json` |
| `frontend/.next/` | Local build cache/output — the host runs `next build` itself |
| `frontend/tsconfig.tsbuildinfo` | Local TypeScript incremental-build cache |
| `__pycache__/`, `.pytest_cache/` | Python runtime caches |
| `.vercel/` | A local, uncommitted CLI link to a specific Vercel project — not reproducible/meaningful on a fresh checkout, and not a real deployment config (no `vercel.json` exists) |
| `backend/.env` | **Real secrets** — `SECRET_KEY`, `DATABASE_URL` credentials, SMTP password. Never copied. A sanitized `backend/.env.example` is included instead |
| `frontend/.env.local` | Contains the previous local-network backend URL — not a secret in this app, but machine-specific. A sanitized `frontend/.env.example` is included instead |
| `backend/storage/` | **Real upload/import data**, not source code — see "Business data" below. The original `backend/storage/` was left completely untouched; nothing was copied, moved, or deleted there |
| `backend/qa_data_audit_report.md` | A one-off audit report, not application source |
| Root-level audit/status docs (`PROJECT_STATUS.md`, `RBAC_MATRIX.md`, `SECURITY.md`, etc.), `outputs/`, `work/`, `docs/`, root `scripts/*.bat` | Historical working notes and Windows-only local dev helpers — not needed to run the application elsewhere |

**Business data you must still handle separately** (not part of this bundle, and I
did not open/classify individual files): `backend/storage/imports/` in the
original project (~33 MB) is a mix of real import spreadsheets (e.g.
`hospital_master.csv`, `device_model_master.csv`) and what look like repeated QA
test fixtures (`test_import_module14.xlsx`, `seed.xlsx` appearing dozens of times).
Review that folder yourself before deciding what — if anything — needs to be
migrated into the new hosting environment's persistent storage.
`backend/storage/documents/` similarly holds real uploaded documents and must be
migrated the same way. **Neither folder was touched, copied, or deleted during
this process** — the original `backend/storage/` remains fully intact.

## Runtime versions

- **Python 3.12** (the project was built and tested against 3.12.10)
- **Node.js 20 LTS** or newer (Next.js 15 requires Node ≥ 18.18; 20 LTS is the
  safer choice) — `frontend/package.json` pins `next@15.5.23`, `react@19`

## Installing dependencies

```bash
# Backend
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # Linux/macOS
# .venv\Scripts\pip install -r requirements.txt  # Windows

# Frontend
cd frontend
npm install
```

Neither step requires network access to anything beyond PyPI/npm — no credentials
needed for installation itself.

## Environment variables

Copy each `.env.example` to the real filename your platform expects (`.env` for
the backend if it reads a dotenv file locally, or set each variable directly
through the platform's own secret/environment configuration UI — **never commit
`.env` or paste real values into a public repo**).

**Backend** (`backend/.env.example`): `DATABASE_URL`, `SECRET_KEY`, `ENVIRONMENT`,
`CORS_ORIGINS`, `FRONTEND_BASE_URL`, `BACKEND_HOST`, `BACKEND_PORT`,
`FILE_STORAGE_PATH`, `AI_PROVIDER`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USE_TLS`,
`SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_FROM_NAME`,
`WHATSAPP_AUTOMATION_HEADLESS`.

**Frontend** (`frontend/.env.example`): `NEXT_PUBLIC_API_URL` — **must** point at
the backend's own origin with no trailing `/api` (every backend route already
starts with `/api/v1`). Next.js inlines `NEXT_PUBLIC_*` values at build time, so
changing this requires a rebuild, not just a restart.

## Database (PostgreSQL) and migrations

The application targets **PostgreSQL only** — there is no SQLite fallback outside
the automated test suite. Provision a managed PostgreSQL instance on your chosen
platform, point `DATABASE_URL` at it, then apply the schema:

```bash
cd backend
alembic upgrade head
```

Run this as an explicit, reviewed deploy step — **not** automatically on every
app boot. (The backend's own startup handler does run an idempotent
roles/permissions/service-visit-type *seed* on every boot, which is safe and
intentional; that's separate from schema migrations.)

Create the first Super Admin user with `python backend/scripts/create_admin.py`
after migrations are applied.

## Frontend/backend URLs and CORS

Before going live, update:
- `CORS_ORIGINS` (backend) — your real production frontend URL(s), comma-separated
- `FRONTEND_BASE_URL` (backend) — same production frontend URL; this is embedded
  into every newly generated QR code's scan URL
- `NEXT_PUBLIC_API_URL` (frontend) — your real production backend URL

Using `CORS_ORIGINS=*` is rejected at startup by design (`backend/app/config.py`)
because this app sends credentials with every request — list explicit origins.

## Persistent storage for uploads

`backend/app/main.py`'s startup handler creates `storage/documents/` and
`storage/imports/` under `FILE_STORAGE_PATH` if missing, and the app writes real
uploaded files there at runtime. **This needs genuinely persistent, writable
storage** — not ephemeral container disk that resets on every redeploy. Depending
on your platform, that means either a persistent volume mount, or migrating this
to object storage (S3-compatible) and adjusting `FILE_STORAGE_PATH`/the document
service accordingly (not currently implemented — the app assumes a local
filesystem path today).

## QR public URLs

QR images encode `<FRONTEND_BASE_URL>/scan/device/<token>` at generation time.
Once `FRONTEND_BASE_URL` is set correctly in production, every *newly generated*
QR code will embed the right URL automatically. Existing QR tokens themselves are
unaffected by this setting — only the URL they're rendered into changes, so no
re-provisioning of existing assets' QR identities is needed.

## WhatsApp Web automation — real limitation, not yet production-verified

The Playwright-based WhatsApp Web automation (`backend/app/services/
whatsapp_automation_service.py`) needs:
1. A real, continuously-running Chromium process (not a stateless/serverless
   function),
2. A **persistent** browser session-profile directory on disk, so the logged-in
   WhatsApp Web session survives process restarts, and
3. The Chromium browser binary itself installed on the host (`playwright install
   chromium` — this is a ~700 MB download, separate from `pip install`, and is
   **not** included in `requirements.txt`'s install step by itself).

This rules out pure serverless/edge platforms for the backend if you want this
feature live. It also means: **do not describe WhatsApp automation as
production-ready until you've actually connected a real session and sent one real
test message on the target hosting environment** — this bundle ships the code,
not a verified-working deployment of it. The same honesty applies to SMTP email:
the code is implemented, but nothing has been verified against a real mailbox
from this hosting copy.

## What I have *not* done

- Not installed dependencies in this copy, not run a build, not started any
  server from `hosting/` against a real database (see
  `DEPLOYMENT_CHECKLIST.md` for what I *did* verify safely).
- Not chosen or configured any specific hosting provider — this bundle is
  deliberately platform-neutral.
- Not touched, copied, or modified the original project's `backend/storage/`,
  database, or git history in any way.
