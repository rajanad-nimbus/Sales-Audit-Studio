# Nimbus Sales Audit

Agentic financial exception management for retail commerce. Lifecycle:
**Case → Evidence → Diagnosis → Policy → Decision → Action → Validation**.
Design docs: `NIMBUS_OVERVIEW.md`, `NIMBUS_ARCHITECTURE.md`, `NIMBUS_DATA_MODEL.md`, `NIMBUS_AGENTS.md`, `NIMBUS_INTEGRATIONS.md`, `NIMBUS_SCENARIOS.md`.

## Stack

FastAPI + async SQLAlchemy + PostgreSQL 18 (backend), Next.js 16 / React 19 (frontend, AdminLTE-style theme), Docker Compose.

## Run

```bash
docker compose up -d --build
curl -X POST http://localhost:8001/api/seed     # load demo cases (or use "Load demo data" on /cases)
```

- Frontend: http://localhost:3001
- API docs: http://localhost:8001/docs

## What is implemented

- **Data model**: source records, canonical transactions (with lineage to their source record), exceptions, cases, evidence, findings, recommendations, decisions, workflows, validation obligations, policy evaluations, connectors, append-only audit events.
- **Ingestion** (`backend/ingest.py`): archives every payload with a SHA-256 hash, skips exact duplicates, quarantines malformed or conflicting records with a reason, and normalizes POS and processor records into signed-decimal canonical transactions. A synthetic feed generator injects realistic anomalies.
- **Reconciliation** (deterministic, idempotent): duplicate sales, unmatched sale, missing refund, amount mismatch, timing difference, orphan payment. Raises one case per store/day/type.
- **Completeness and balancing controls**: POS control manifests (`POSControl`: declared count and total per store/day) are compared with ingested POS transactions -> `INCOMPLETE_FEED` / `BALANCING_VARIANCE`.
- **Bank and ERP/GL reconciliation**: bank deposits are compared with matched processor net, and ERP postings with matched POS net, per store/day -> `BANK_VARIANCE`, `MISSING_DEPOSIT`, `GL_VARIANCE`, `MISSING_GL_POSTING`. Feeds are pushed via `POST /api/ingest` (`POS`, `Processor`, `POSControl`, `Bank`, `ERP`); the simulator uses a fresh business day per run.
- **Ontology service** (`backend/ontology.py`, `GET /api/ontology`): exception families, evidence requirements, known causes and dispositions are read from it by the agents. It is an in-process stand-in, also exposed over MCP (`python backend/mcp_server.py`, stdio; tools `list_exception_types`, `get_exception_type`, `get_evidence_requirements`).
- **Agents**: rule-based Investigation, Resolution and Validation agents. Investigation optionally drafts finding prose with Claude when `ANTHROPIC_API_KEY` is set (figures and authority stay deterministic; untested without a key).
- **Policy gate** (deterministic, `policy-v1.0`): Permit / RequireHuman / RequestEvidence.
- **Workflow**: approve -> policy -> workflow -> Resolution executes -> validation obligation -> Validation verifies -> case closed, every step audited.
- **Auth**: Ontology Studio is Nimbus's authentication authority. Sign in through Ontology Studio and provide its JWT as the Nimbus bearer token. Nimbus verifies it at Ontology Studio's `/api/v1/auth/me` endpoint for each protected request, then maps Ontology roles to `finance`, `it`, or `admin` through `NIMBUS_ONTOLOGY_ROLE_MAP`. Click the user card in the sidebar to set the token.
- **Action Center** (`/actions`, built for hundreds of stores and millions of transactions): everything is server-side. Filters (stage, type, store, priority, owner, SLA, amount, dates, search), facets with counts, sortable paginated queue (max 100/page), grouping by type/store/day/priority/assignee, summary tiles, saved views, keyboard shortcuts, CSV export (up to 50k rows), a case preview drawer, and **bulk actions** (assign, investigate, approve, escalate, advance, snooze, unassign) with a confirmation step. Safeguards: 1,000 cases per bulk run, 200 for agent/decision actions, bulk approval skips financial movement above $5,000 or failed policy and reports why. Load-tested at 200,000 cases: every query under 0.3 s.
- **Daily batch**: transaction logs are synced once a day. `python backend/batch_job.py --date YYYY-MM-DD` (or `POST /api/batch/run`) loads the day's feeds and reconciles; runs are recorded (`/api/batch/runs`), idempotent per business date, and the UI shows "data as of" plus a stale/failed warning (stale after 26h). SLA clocks start when cases are raised (Critical 4h, High 24h, Normal 72h).
- **Audited record**: every action (system, agent, human, export) is written to an append-only audit trail. The database itself enforces this: each event gets a sequence number and a SHA-256 hash chained to the previous event, and triggers reject any UPDATE, DELETE or TRUNCATE. `GET /api/audit/verify` recomputes the whole chain in SQL and reports the first broken event; the Audit Trail page shows the result, supports search and filters, and exports CSV/JSON that include the hashes and the chain head. A database superuser can still disable triggers, so for stronger guarantees revoke UPDATE/DELETE from the app role and periodically record the head hash in an external system.
- **Downstream export** (`/exports`): destinations are webhooks (HTTP POST, signed with HMAC-SHA256 in `X-Nimbus-Signature`, with an `X-Nimbus-Idempotency-Key`) or file drops (written to `./exports/<destination>/` with a `.sha256` file). Datasets: `resolutions` (closed, validated cases), `adjustments` (approved financial journal lines), `audit` (events since the last delivery). Exports are incremental and idempotent (no record is ever delivered twice to the same destination), capped at 5,000 records per batch, track Pending/Sent/Acknowledged/Failed with retry and manual acknowledgment, and are themselves audited. A built-in test receiver (`/api/exports/_sink`) verifies signatures. Webhook secrets are stored in the database as plain text in this demo; use a secrets manager in production.
- **Migrations**: the backend container runs `alembic upgrade head` on start; the app no longer creates tables itself.
- **Nimbus Assistant** (header > Assistant; modelled on the Ontology Studio assistant): multiple saved conversations with search, history and auto-titles; a context chip (all cases or the selected case) that locks once you send a message; a Finance / IT perspective picker; grounded greeting and capability cards; formatted answers (direct answer, supporting bullets, "Next" step) with source chips linking to cases and the ontology; follow-up suggestions; copy, regenerate and thumbs actions; copy the conversation as Markdown; docked or floating window with a minimize launcher. Cases can be mentioned by number. It asks which case you mean when a question is ambiguous, answers only from live data, and never approves or changes anything. Answers come from rules by default; with `ANTHROPIC_API_KEY` set, Claude drafts them from the same data under the same constraints (untested without a key). Conversations are stored in the browser.
- **UI**: live Command Center (Finance / IT views, pipeline, decision queue with consequence preview, agent feed, Autopilot, Ask Nimbus), Cases, Data Pipeline, Audit Trail.

## Not yet implemented (needs external systems)

- Pull connectors to real POS, payment processor, bank and ERP endpoints. The ingest API and canonical mapping are the integration surface; adapters need credentials and file/API specs from each system.
- Production identity (SSO/OIDC). Bearer tokens with roles are in place; an IdP is needed to replace them.
- Nimbus retains a built-in deterministic exception catalog for availability. To read the approved Nimbus Sales Audit ontology, set `NIMBUS_ONTOLOGY_API_URL` and `NIMBUS_ONTOLOGY_API_KEY` to an Ontology Studio `/api/integration/v1` endpoint and an `ons_...` agent key. Nimbus sends a bearer key and retrieves business rules, entities, relationships, metrics, glossary terms, and workflow metadata from one approved release, following cursor pagination. Incomplete controls, mixed releases, revoked credentials, and failed refreshes invalidate the active context and retain local deterministic controls. Check or refresh the connection with `GET` / `POST /api/ontology/integration/{status|refresh}` (IT/Admin only).

## Migrations

```bash
docker compose run --rm --no-deps -v "$PWD/backend:/app" backend alembic revision --autogenerate -m "change"
docker compose run --rm --no-deps backend alembic upgrade head
```

Revisions: `0001` baseline, `0002` canonical transaction -> source record link. The app still calls `create_all` on startup, which only creates missing tables; run `alembic upgrade head` for column changes.


### Enterprise ontology and agent audit integration

The existing Sales Audit ontology in Ontology Studio is the enterprise source. Nimbus does not create or publish another ontology. `GET /api/ontology/integration/context` returns the definitions visible to the configured integration persona, with workspace, approved release, version, and content hash. `POST /api/ontology/integration/refresh` refreshes that view atomically; readiness requires all expected Sales Audit control codes.

Investigations refresh the context and pass approved business-rule expressions and workflow metadata to both supported model transports as reference data. Local evidence requirements, tool scopes, deterministic financial controls, and human approval gates still apply. Shared audit events record `after.ontology_context`; model runs retain the exact context identity captured before their invocation. Historical events are unchanged. Provenance uses the existing append-only audit database and its hash chain, with no parallel audit store or migration.

`GET /api/agent/ontology` compares live agent behavior and all SQLAlchemy database models—including audit events, policy evaluations, workflows, validation obligations and settings—with visible enterprise entity names. It lists foreign keys and missing definitions without returning business records. Name matches are candidate bindings, not approved property mappings. A missing definition may reflect integration-persona restrictions.

Ontology Studio's current read-only REST API excludes agent-policy rules and returns workflow metadata without process steps or action contracts. Those capabilities are explicitly reported as gaps; this integration does not interpret prose as executable policy or claim that remote processes are running. The existing local exception catalog remains the operational fallback until equivalent structured exception definitions are exposed by Studio.


For the local Docker deployment, Nimbus reaches Ontology Studio at `http://host.docker.internal:8000/api/integration/v1` (the browser UI is `http://localhost:3050`). The Compose default uses that endpoint, and Nimbus refreshes ontology context at startup. The endpoint and credential configuration are reported separately in health status.

The deployed `Nimbus Sales Audit` workspace (ID 100) was verified on 2026-10-06: 13 entities, 17 controls, and one lifecycle process. It was still draft, with no release, persona, or agent key. Before live consumption, complete the Studio workspace review/release flow, configure an approved persona scoped to Sales Audit and its lifecycle process, and issue its integration key through Access & Governance. Store that key as `NIMBUS_ONTOLOGY_API_KEY` in `.env`, then recreate the Nimbus backend. A reachable server alone does not make an approved context available.


The Sales Audit draft was subsequently extended in the deployed Studio database on 2026-10-06 using `ontology/extend_sales_audit.py` and `ontology/runtime_schema.json`. It now contains 42 entities, 407 properties, 28 entity relationships, 15 personas, 17 processes, eight agent behavior policies, and nine disabled agent consumers. Six human personas document Finance, Analyst, Auditor, IT, Store Manager and Admin responsibilities; nine system personas cover the six core agents plus ingestion, export and the read-only assistant. Subprocess steps name native persona codes and link to the existing case lifecycle so Studio derives process participation.

The extension derives persistence properties and foreign-key relationships from the running Nimbus schema. Studio permissions use its supported `read` action; operational responsibilities are documented separately and remain enforced by Nimbus roles. Processes are responsibility models, not executable action contracts. No user memberships, credentials or approvals were created. Native persona validation, relationship integrity and an idempotent rollback-only rerun passed. Two maintenance audit entries record the update and permission normalization; a pre-change export was preserved at `/private/tmp/nimbus-sales-audit-before-extension.json` on the host.

For future maintenance, the script defaults to rollback-only preview and requires explicit `--apply` plus a new backup path to commit. It refuses a workspace that is not the existing editable Sales Audit draft. Existing definitions are preserved; the initial permission normalization is narrowly restricted to records generated by this extension. Review/release and agent activation remain separate Studio lifecycle operations.
