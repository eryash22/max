# Deployment Checklist

Platform-neutral — no hosting provider has been selected yet. Work through this
before pushing to GitHub / deploying anywhere.

## Before pushing to GitHub

- [ ] Review `backend/.env.example` and `frontend/.env.example` — confirm no real
      value was accidentally left in either (both were generated from sanitized
      templates; spot-check anyway).
- [ ] Decide what to do about `backend/storage/imports/` and
      `backend/storage/documents/` in the **original** project — this bundle
      deliberately does not include them. Review which files are real business
      data worth preserving vs. QA test debris, independently of this copy.
- [ ] Confirm `.gitignore` is in place (it is, at `hosting/.gitignore`) before your
      first `git init` / `git add` in this folder, so you don't accidentally
      commit a `.env` you create locally while testing.
- [ ] `git init` inside `hosting/`, review `git status` once before the first
      commit to make sure nothing unexpected got staged.

## Database

- [ ] Provision a managed PostgreSQL instance on your chosen platform.
- [ ] Set `DATABASE_URL` to point at it.
- [ ] Run `alembic upgrade head` as an explicit, reviewed step — not automatically
      on every deploy.
- [ ] Run `python backend/scripts/create_admin.py` to create the first Super Admin.
- [ ] Confirm `ensure_roles_and_permissions`/`ensure_default_service_visit_types`
      (the app's own startup seed) ran successfully — check backend logs on first
      boot.

## Backend

- [ ] Install dependencies: `pip install -r backend/requirements.txt`.
- [ ] Set every variable listed in `backend/.env.example` to a real production
      value via your platform's secret manager.
- [ ] Set `ENVIRONMENT=production`.
- [ ] Set `CORS_ORIGINS` to your real frontend URL(s) — not `*`, not `localhost`.
- [ ] Set `FRONTEND_BASE_URL` to your real frontend URL (used in generated QR
      codes).
- [ ] Provision **persistent** storage for `FILE_STORAGE_PATH` (default
      `./storage`) — do not deploy to a platform with ephemeral disk unless
      you've separately migrated uploads to object storage.
- [ ] Start command: `uvicorn app.main:app --host 0.0.0.0 --port <port>` (no
      `--reload` in production).
- [ ] Decide now whether WhatsApp Web automation will run in this environment —
      if yes, also provision a persistent volume for its session-profile
      directory and run `playwright install chromium` as part of the build/setup
      step (not covered by `pip install` alone). If no, leave it unconfigured;
      the app degrades gracefully (email/manual WhatsApp-link flows still work).
- [ ] If you want the contract-expiry reminder ladder (30/7/1 day) to actually
      fire, set up an external scheduler (platform cron, GitHub Actions on a
      schedule, or similar) that calls `POST /api/v1/contracts/run-expiry-scan`
      on a daily cadence — nothing in the code does this automatically.

## Frontend

- [ ] Install dependencies: `npm install`.
- [ ] Set `NEXT_PUBLIC_API_URL` to your real backend URL (no trailing `/api`).
- [ ] Build: `npm run build`.
- [ ] Start command: `npm run start` (needs a Node runtime — this app has dynamic
      routes, e.g. `/assets/[id]`, so it cannot be deployed as a pure static
      export).

## After first deploy — verification, not assumption

- [ ] Load the frontend URL in a real browser; confirm the login page renders
      with no console errors.
- [ ] Log in with the Super Admin account created above; confirm
      `/api/v1/auth/me` succeeds and the dashboard loads real data.
- [ ] Confirm `/api/v1/...` routes resolve correctly and there is no accidental
      `/api/api/v1/...` double-prefix (check your reverse proxy / platform routing
      config if you put one in front of the backend — the application code itself
      has no such bug).
- [ ] Generate one QR code for a test asset and confirm the encoded URL uses your
      real production `FRONTEND_BASE_URL`, not a placeholder.
- [ ] If SMTP is configured, send exactly one controlled test email through the
      actual notification pipeline and confirm real delivery — don't assume it
      works because the code exists.
- [ ] If WhatsApp automation is enabled, connect a real session and send exactly
      one controlled test message through the actual event pipeline, and verify
      DOM-confirmed delivery — don't assume it works because the code exists.
- [ ] Migrate whatever real data you identified from the original project's
      `backend/storage/` into the new environment's persistent storage, and the
      original database's real records (if any) via a proper Postgres
      dump/restore — both are independent of this code deployment.

## What was safely verified before this checklist was written

- The original project's full file tree, `git status`, and `backend/storage/`
  were inspected but never modified, moved, or deleted.
- This copy's structure, manifests, and the presence of every file the
  application's own imports/config reference were checked against the original
  working tree (see the accompanying report for exact findings) — without
  installing dependencies or connecting to the live development database from
  this copy.
