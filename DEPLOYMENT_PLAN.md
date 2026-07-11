# TriageAI — AWS Production Deployment Plan

> Authored July 2026. Target: deploy TriageAI to AWS the way a production agentic AI application is deployed, within roughly $10 to $15 per month, using Terraform, while upgrading the codebase and Streamlit UI so the project reads as senior AI Engineer work.

---

## 1. Guiding principles

1. **Few services, each defensible.** Every AWS service in this plan earns its place. Final list: ECR, App Runner, ECS on Fargate (Spot, one internal service), SSM Parameter Store, CloudWatch, IAM (GitHub OIDC), S3 + DynamoDB (Terraform state), AWS Budgets. Nothing else.
2. **Keep what already works.** Supabase stays as the Postgres + auth layer (the code already supports `PostgresSaver` via `DATABASE_URL`). Renting RDS to replace a free managed Postgres would be cost theater, and saying so in an interview is a strength, not a weakness.
3. **Evals gate deployment.** The existing `run_langsmith_eval.py` safety-recall hard gate (exit non-zero below 1.00) becomes a required CI step before any image ships. Eval-gated deployment is the single most differentiating practice here; very few portfolio projects have it.
4. **Preserve the fail-open design.** All CLAUDE.md gotchas hold: lazy imports, silent fallbacks, safety screen defaults to `False` on outage, no startup blocking on missing keys.

---

## 2. Target architecture

```
                         ┌─────────────────────────────────────────────┐
                         │                 AWS (us-east-1)             │
  Patient / Staff        │                                             │
       │                 │  ┌──────────────────┐   Streamable-HTTP    │
       ▼  HTTPS          │  │  App Runner      │   (bearer token)      │
  Custom domain ────────►│  │  triageai-app    │──────────────────┐    │
  (managed TLS)          │  │  Streamlit + graph│                 ▼    │
                         │  └────────┬─────────┘   ┌──────────────────┐│
                         │           │             │ ECS Fargate Spot ││
                         │           │             │ triageai-mcp     ││
                         │           │             │ FastMCP + Chroma ││
                         │           │             └──────────────────┘│
                         │           │                                 │
                         │   SSM Parameter Store (secrets)             │
                         │   CloudWatch (logs, alarms, dashboard)      │
                         │   ECR (both images)                         │
                         └───────────┼─────────────────────────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              ▼                      ▼                      ▼
        Supabase Postgres      Gemini API             Phoenix (self-hosted)
        (auth, messages,       (LLM calls)            (OTel traces, evals,
         LangGraph checkpoints)                        staff-feedback annotations)
```

### 2.1 Service choices and rationale

| Concern | Choice | Why (and what to say in interviews) |
|---|---|---|
| Public compute | **AWS App Runner** for the Streamlit + LangGraph container | Managed HTTPS, custom domain, autoscaling, per-request vCPU billing. Roughly $5 to $7 per month for a 2 GB instance at portfolio traffic. Avoids the two big fixed costs: ALB (~$18/mo) and NAT gateway (~$32/mo). The interview line: "I chose App Runner over ALB + Fargate for the edge because at my traffic profile the load balancer would cost more than all compute combined; the same image redeploys to ECS behind an ALB unchanged when traffic justifies it." |
| Internal tool plane | **ECS Fargate Spot** for the MCP tool server | Gets real ECS/Fargate on the resume, costs ~$2 to $3 per month at 0.25 vCPU / 0.5 GB Spot, and is the architecturally honest home for an internal service. Reached from App Runner through a VPC connector + Cloud Map service discovery (default VPC, no NAT: the task only needs outbound via public subnet). |
| Checkpoints + app data | **Supabase Postgres** (existing) | `PostgresSaver` support is already in the code. HITL interrupts must survive restarts; SQLite inside a container does not. Zero added cost. Document RDS Postgres as the company-context equivalent. |
| Vector store | **ChromaDB baked into the MCP image at build time** (current approach, kept) | The policy corpus is 8 static markdown docs. Rebuilding the store per image build is deterministic, offline-safe, and versioned with the code. Migration to pgvector on Supabase is a documented phase 3 option if the corpus grows or needs runtime writes. |
| Secrets | **SSM Parameter Store** (SecureString) | Free tier covers this entirely; Secrets Manager charges $0.40/secret/month for rotation this project does not need. Injected as env vars by App Runner / ECS task definitions. No `.env` in images (already enforced by `.dockerignore`). |
| Registry | **ECR** with lifecycle policy (keep last 5 images) | Standard. Lifecycle policy keeps storage pennies. |
| Observability | **OpenTelemetry** (OpenInference instrumentation) → **self-hosted Arize Phoenix**; **CloudWatch** logs + alarms | LangSmith free tier is exhausted, and the migration is the stronger story anyway: instrument once with vendor-neutral OTel, choose the backend by env var (`OTEL_EXPORTER_OTLP_ENDPOINT`). Phoenix is OTel-native, one lightweight container (Fargate Spot, ~$2-3/mo, or local-only during dev), and covers traces, evals, and annotations. CloudWatch answers "is the service up"; Phoenix answers "is the agent good". Alarms: 5xx rate, container restarts, P99 latency, AWS Budgets alert at $15. |
| CI/CD | **GitHub Actions with OIDC role assumption** | No long-lived AWS keys anywhere. This is the current standard and a strong resume line on its own. |
| IaC | **Terraform**, state in S3 + DynamoDB lock | Modules: `ecr`, `apprunner`, `ecs-mcp`, `ssm`, `iam-github-oidc`, `observability`. One `envs/prod` root. An `enable_alb` variable documents (and can provision) the ALB + private subnet upgrade path without paying for it now. |

### 2.2 Considered and rejected (keep this section; it is interview gold)

- **Amazon Bedrock AgentCore Runtime** (GA October 2025, consumption-priced, framework-agnostic, works with LangGraph). Rejected for the primary path because the goal of this project is demonstrating that you can build the serving, state, and tool infrastructure yourself; AgentCore abstracts exactly that away. Named in the README as the managed alternative, which shows you know the 2026 landscape.
- **LangGraph Platform**: same reasoning; also vendor-coupled pricing.
- **AWS Lambda**: 15-minute cap is workable but the image cold-start plus a long-running HITL graph and streaming UI fit containers better; dependency size also pushes past the zip limit.
- **EKS**: absurd overkill for two services; saying "I did not need Kubernetes and I can tell you why" lands better than running it.
- **ALB + Fargate always-on for everything** (~$45 to $60/mo): the correct company-scale answer, provisioned in Terraform behind `enable_alb`, intentionally not paid for at portfolio scale.

### 2.3 Cost estimate (steady state)

| Item | $/month (approx.) |
|---|---|
| App Runner (1 instance, 1–2 GB provisioned memory at ~$0.007/GB-hr, light active vCPU) | 5 – 10 |
| ECS Fargate Spot (0.25 vCPU / 0.5 GB, 24/7) | 2 – 3 |
| ECR storage, CloudWatch, SSM, S3 state | 1 – 2 |
| Supabase, LangSmith, Resend | 0 (free tiers) |
| Gemini API | usage-based, low single digits |
| **Total** | **~$8 – 15** (start at 1 GB; bump to 2 GB only if the container needs it) |

Verify current prices when implementing; these are July 2026 approximations.

---

## 3. Code modifications (prioritized)

### M1 — Promote MCP to a standalone networked service (the flagship change)

Current state: both MCP servers run as **stdio subprocesses inside the app container**, which makes MCP an implementation detail. Production MCP in 2026 runs over **Streamable HTTP** as an independently deployable service.

Work items:

1. In `mcp_tools/mcp_server.py`, support `mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)` behind an env flag (`MCP_TRANSPORT=streamable-http|stdio`). Keep stdio as the local-dev default so nothing existing breaks.
2. Fold the Chroma policy search into this same FastMCP server (it already exposes `search_hospital_policy`); drop the separate `chroma-mcp-server` process in cloud mode. One tool server, one container.
3. Make `mcp_config.json` env-overridable: when `MCP_SERVER_URL` is set, `graph/workflow.py:_init_mcp_tools` builds the `MultiServerMCPClient` config with `{"transport": "streamable_http", "url": ...}` instead of stdio commands. `langchain-mcp-adapters` already supports this transport.
4. Add bearer-token auth: MCP server checks `Authorization: Bearer $MCP_AUTH_TOKEN`; the client sends it via headers. Token lives in SSM.
5. Add `/health` (FastMCP custom route) for the ECS health check.
6. New `Dockerfile.mcp` (slim: fastmcp, chromadb, supabase client only) plus a split `requirements/` layout: `base.txt`, `app.txt`, `mcp.txt`, `dev.txt`. Both images shrink substantially.
7. Preserve the full fallback chain: MCP-over-HTTP unreachable → local `TRIAGE_TOOLS` → `_run_fallback`. The graceful-degradation story gets *stronger*: the app now survives the death of a whole dependency service, and you can demo that live.

Resume line: "Extracted the agent's tool plane into an independently deployed MCP server (Streamable HTTP, bearer auth, health-checked on ECS Fargate), with graceful degradation to in-process tools on outage."

### M2 — Postgres checkpointer as the cloud default

- Set `DATABASE_URL` (Supabase pooler, port 6543, `PostgresSaver`) in App Runner env. Code path already exists.
- Stop tracking `data/checkpoints.db` in git (`git rm --cached`, add to `.gitignore`). It is runtime state, not source.

### M3 — Centralized settings

- Add `config.py` using `pydantic-settings`: one typed `Settings` object for every env var currently read ad hoc (`LLM_GEMINI_API_KEY`, `LLM_MODEL`, `DATABASE_URL`, `SUPABASE_*`, `RESEND_API_KEY`, `MCP_SERVER_URL`, `MCP_AUTH_TOKEN`, `LANGSMITH_*`). Defaults preserve current fallback behavior. Modules import settings lazily to keep the no-key startup guarantee.

### M4 — OpenTelemetry tracing + structured logging (replaces LangSmith)

Context: the LangSmith free tier is exhausted, which means tracing is currently dark **and the CI eval gate (`run_langsmith_eval.py`) cannot run**. This migration is therefore pre-deploy and load-bearing, not a nice-to-have.

1. **New `telemetry.py`**: one env-gated, fail-open init function. `OTEL_ENABLED=1` → OTLP exporter to `OTEL_EXPORTER_OTLP_ENDPOINT` + two auto-instrumentors: `openinference-instrumentation-langchain` (covers the whole LangGraph run) and the google-genai instrumentor (covers the direct `genai.Client` structured-output calls). Roughly 15 lines; any failure logs a warning and the app runs untraced, consistent with the fail-open convention.
2. **Backend: self-hosted Arize Phoenix** (OTel-native, single container). Local dev: `docker run arizephoenix/phoenix` in docker-compose. Cloud: third small service on Fargate Spot (~$2-3/mo) or keep it local-only and export prod traces later. Vendor-neutral by construction: switching to Langfuse, Grafana Tempo, or CloudWatch OTLP is an env-var change, no code.
3. **Replace LangSmith touchpoints**: `@traceable` decorators → OTel spans (or drop; the instrumentors already trace these calls); `collect_runs()` run-id capture in `streaming.py`/`workflow.py` → current OTel `trace_id`; `_log_staff_feedback` → span attributes (`staff_approved`, `draft_edit_ratio`) or Phoenix annotations. Same fail-open guarantee: feedback logging must never break the send path.
4. **Rewire the CI eval gate off LangSmith**: `run_eval.py` already runs the labeled datasets locally with no LangSmith dependency. Port the `safety_recall == 1.00` hard gate and the code-based evaluators from `eval_evaluators.py` into the local harness; LLM-judge evaluators run against local outputs. `run_langsmith_eval.py` and `sync_langsmith_dataset.py` stay in the repo as the "if you have LangSmith" path, documented as such. Online judges (`online_evaluators.py`, formerly LangSmith automations) become a scheduled job sampling Phoenix traces, or Phoenix evals.
5. **Structured logging**: replace scattered `print`/operational messages with a JSON logger (`structlog` or stdlib): `event`, `thread_id`, `trace_id`, `node`, `duration_ms`, `urgency`. CloudWatch Logs Insights then answers "P95 time in triage_agent" in one query; `trace_id` in every log line links logs to Phoenix traces. Keep `warnings.warn` for the import-fallback paths (documented convention).

Effort: ~2 days. Resume line: "Migrated agent observability from LangSmith to vendor-neutral OpenTelemetry (OpenInference) with a self-hosted Phoenix backend, and decoupled the CI safety-eval gate from any SaaS dependency."

### M5 — Engineering hygiene (cheap, high signal)

- Add `pyproject.toml` with `ruff` (lint + format) and pin dependencies with a lock (uv or pip-tools). Currently there is no linter and no pins; every requirement is `>=`, so builds are not reproducible.
- CI runs: `ruff check`, `python tests/test_tools.py`, then the eval gate.
- Remove the hardcoded `_FIXED_RECIPIENT` email redirect in `mcp_tools/tools/communication.py` by verifying a domain in Resend (or an SES sandbox note); at minimum, gate it behind `EMAIL_TEST_MODE=1`.

### M6 — Security touches

- Re-enable `enableXsrfProtection = true` in `.streamlit/config.toml` (currently disabled).
- App Runner instance role and ECS task role scoped to exactly the SSM parameters they read. No wildcard IAM.
- `git-secrets` or gitleaks step in CI.

### M7 — RAG v2: make policy retrieval a real pipeline (pre-deploy)

Current state (`agents/policy_agent.py`, `scripts/seed_policy.py`) is naive RAG: fixed 700-char chunks that ignore markdown structure, ChromaDB's default MiniLM embedder, raw top-3 cosine similarity with no relevance floor, no hybrid search, no reranking, no citations, and no retrieval metrics. This ships **before** deploy because M1 bakes the vector store into the MCP image (rebuild it once, with the final pipeline) and because the CI eval gate should measure retrieval from the first deploy.

Work items:

1. **Structure-aware ingestion**: split `data/policies/*.md` on headers (e.g. LangChain `MarkdownHeaderTextSplitter` or ~30 lines of stdlib), then size-cap within sections. Metadata per chunk: `source`, `section_title`, `policy_category` (refill, appointment, billing, emergency, ...), `effective_date` if present.
2. **Hybrid retrieval**: dense (Chroma) + BM25 (`rank_bm25`, in-process; the corpus is 8 docs / ~100 chunks) fused with reciprocal rank fusion. Clinical policy text is exactly where lexical matching rescues dense misses (drug names, "90-day supply", form numbers).
3. **Relevance floor**: below a distance threshold, return nothing and let the draft say no applicable policy was found. Today three chunks always come back, relevant or not, and get stuffed into the draft prompt: a silent hallucination vector.
4. **Query construction**: build the retrieval query from triage `intent` + extracted entities rather than concatenating the raw message + summary.
5. **Citations end to end**: `get_relevant_policy` returns `(text, source, section)` tuples; `generate_draft_reply` cites the policy name; the staff UI shows "Grounded in: refill_policy.md § Controlled substances" next to the draft. High demo value, near-zero effort once metadata exists.
6. **Retrieval eval in the CI gate**: a small labeled set (query → expected policy doc/section, ~30 cases mirroring the existing eval-dataset style) scoring recall@3 and precision@3 via `scripts/run_eval.py`-style harness. This is the differentiator; most candidates cannot show retrieval metrics.
7. Keep the local ONNX embedder deliberately: offline-safe cold starts, zero per-query cost, consistent with the fail-open design. Document Gemini embeddings and pgvector as the scale-up path, with the condition that would trigger each.

Deferred (post-deploy): cross-encoder or LLM reranking, multi-query/query rewriting, semantic caching of policy lookups, pgvector migration.

Resume line: "Rebuilt policy retrieval as a hybrid (BM25 + dense, RRF) pipeline with structure-aware chunking, relevance thresholds, and per-reply source citations, gated in CI by recall@3 on a labeled retrieval set."

### M8 — Deferred to phase 3 (documented, not built now)

- FastAPI service layer between UI and graph (SSE streaming, versioned REST for submit/resume/approve), then any frontend can replace Streamlit. This is the strongest long-term profile move; the plan records the intended API shape so the Streamlit restyle does not paint you into a corner.
- Input-moderation guardrail node, multi-region.

---

## 4. Streamlit UI upgrade spec

Current state: default theme, `layout="centered"` (cramps the two-pane staff dashboard), emoji-driven urgency coding, no visual hierarchy. Target: a calm clinical product look, not a hackathon demo.

1. **Theme** (`.streamlit/config.toml`): custom palette. Suggested: background `#F7F9FB`, surface white, primary `#0F6FFF` or a clinical teal `#0B8A8F`, text `#1A2B3C`, font "Inter" (via `[theme] font` + CSS import). Dark sidebar optional.
2. **Layout**: `layout="wide"` with a max-width wrapper for patient chat; staff dashboard uses the full width. Move login to a centered card with the logo from `assets/`.
3. **Urgency as pill badges, not emoji**: small CSS class per level (EMERGENCY red `#DC2626`, HIGH amber `#D97706`, NORMAL blue `#2563EB`, LOW gray `#6B7280`), injected once via `st.markdown(<style>)`. One `render_urgency_badge(urgency)` helper replaces `_urgency_emoji` everywhere.
4. **Staff dashboard**: metrics row at top (`st.metric`: open messages, pending approvals, emergencies today, median response); queue entries as bordered cards (`st.container(border=True)`) with badge, name, snippet, timestamp; detail pane with clear section separators and the draft reply in a highlighted editable card.
5. **Patient chat**: avatars (`st.chat_message(avatar=...)`), `st.status` blocks for the streaming stage labels ("Analyzing your message", tool calls) instead of raw captions, and a distinct styled banner when the checklist interrupt asks a follow-up question.
6. **Pending approvals**: same card system, primary/secondary button hierarchy (Approve = primary, Dismiss = tertiary), confirmation toast (`st.toast`) after actions.
7. **Polish**: favicon + page title per view, hide Streamlit chrome (menu/footer) via CSS, consistent empty states with a small illustration or icon rather than bare captions.

Keep all logic untouched; this is a presentation-layer pass over `streamlit_app.py` plus one new `app/ui.py` for CSS and badge/card helpers.

---

## 5. CI/CD pipeline (GitHub Actions)

```
PR:    ruff → unit tests (tests/test_tools.py) → offline eval, code-only judges
       (local harness per M4.4 — no SaaS dependency; safety_recall == 1.00 is a hard gate,
        plus retrieval recall@3 once RAG v2 lands)

main:  everything above
       → build app + mcp images, push to ECR (tags: sha + latest)
       → terraform plan/apply (envs/prod)
       → App Runner + ECS pick up new images
       → smoke test: hit /health on MCP, /_stcore/health on app, submit one synthetic
         NORMAL-urgency message end-to-end and assert a triage_result comes back
```

Auth via `aws-actions/configure-aws-credentials` with an OIDC-federated IAM role restricted to this repo and branch. The existing `.github/workflows/eval.yml` becomes the reusable eval job.

---

## 6. Repository restructuring

```
TriageAI/
├── app/  agents/  graph/  schemas/  scripts/  tests/        (unchanged)
├── mcp_tools/            + streamable-http entrypoint, /health
├── requirements/          base.txt, app.txt, mcp.txt, dev.txt (+ lock)
├── config.py              pydantic-settings (M3)
├── Dockerfile             app image (existing, trimmed)
├── Dockerfile.mcp         tool-server image (new)
├── deploy/
│   └── docker-compose.yml two services + shared network (mirrors prod topology locally)
├── infra/                 Terraform
│   ├── modules/{ecr,apprunner,ecs-mcp,ssm,iam-github-oidc,observability}
│   └── envs/prod/
├── .github/workflows/{ci.yml,deploy.yml,eval.yml}
└── pyproject.toml         ruff config, project metadata
```

Also move the capstone-artifact markdown files (`alphaeval.md`, `milestone3-progress.md`, `codebase-summary.md`, `ERRORS.md`, `instructions/`, `report/`) into `docs/` so the repo root reads like a product, not a coursework folder.

---

## 7. Phased roadmap

| Phase | Scope | Effort |
|---|---|---|
| **1. Local prod-parity** | M1 (MCP over HTTP + Dockerfile.mcp), M2, M3, requirements split, docker-compose (app + mcp + phoenix) | 2–3 days |
| **1.5. OTel migration** | M4 items 1–4: telemetry.py, Phoenix, LangSmith touchpoint swap, CI eval gate off LangSmith | ~2 days |
| **2. RAG v2** | M7: structure-aware chunking, hybrid retrieval + RRF, relevance floor, citations, retrieval eval in the CI gate | 2–3 days |
| **3. UI restyle** | Section 4 in full (including citation display next to draft replies) | 1–2 days |
| **4. Infra + CI/CD** | Terraform modules, SSM, OIDC, eval-gated deploy pipeline, first cloud deploy | 3–4 days |
| **5. Observability + hardening** | M4.5 (structured logging), M6, Phoenix on Fargate (if not local-only), CloudWatch dashboard + alarms, Budgets, smoke tests | 1–2 days |
| **6. Docs + story** | Architecture diagram in README, "production decisions" section, demo script, resume bullets | 1 day |
| **7. (Later) API layer** | FastAPI + SSE, frontend-agnostic serving (M8) | 1–2 weeks |

Each phase lands as its own PR with a `DEVELOPMENT.md` sprint entry, continuing the existing decision-log convention.

---

## 8. Resume and interview framing

Bullets this plan makes true:

- "Deployed a HITL agentic triage system to AWS (App Runner + ECS Fargate, Terraform, GitHub OIDC) with eval-gated CI/CD: a 100%-safety-recall hard gate blocks any deploy that regresses emergency detection across 189 labeled adversarial cases."
- "Designed a decoupled tool plane: MCP server over Streamable HTTP with bearer auth on Fargate, with graceful degradation to in-process tools, so the agent survives total tool-service outage."
- "Ran durable LangGraph state on Postgres so human-in-the-loop interrupts (staff approval, patient follow-ups) survive restarts and horizontal scaling."
- "Kept steady-state infrastructure under $15/month by matching services to traffic profile (no ALB/NAT at portfolio scale) while codifying the ALB + private-subnet upgrade path in Terraform."

Interview talking points to rehearse: why App Runner over ALB/Fargate at this scale, why not Bedrock AgentCore or LangGraph Platform, why the safety screen fails closed on emergencies but fail-open on infra, why evals gate deploys, why MCP over HTTP instead of stdio, why Supabase over RDS.

---

## 9. Explicit non-goals

- No EKS, no multi-region, no RDS, no NAT gateway, no Secrets Manager rotation, no service mesh. Each is named in docs with the condition under which it would become the right call. Knowing where the line is *is* the senior signal.
