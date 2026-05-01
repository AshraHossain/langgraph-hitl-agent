# LangGraph HITL Agent Pipeline

Production-grade Human-in-the-Loop RFP analyzer built on **LangGraph** stateful workflows, **PostgreSQL** checkpointing, **Redis** pub/sub for interrupt/resume, **LangSmith** observability, and **FastAPI**.

Applies DO-178C-inspired safety-engineering discipline: deterministic state machine, traceable decision nodes, structured human approval gates, retry budgeting, and a full audit trail.

---

## Architecture

```
        ┌──────────────┐
        │   FastAPI    │  POST /rfp  (upload RFP)
        │              │  POST /approvals/{thread_id}
        │              │  GET  /audit/{thread_id}
        └──────┬───────┘
               │
               ▼
        ┌──────────────────────────────────────┐
        │         LangGraph StateGraph         │
        │                                      │
        │   ingest → extract → analyze →       │
        │   [HITL:approve_risk] → compose →    │
        │   [HITL:approve_final] → finalize    │
        └───────┬────────────────────┬─────────┘
                │                    │
       Postgres │ checkpointer       │ Redis pub/sub
       (durable state + audit)       │ (interrupt/resume signals)
                │                    │
                ▼                    ▼
       ┌──────────────┐      ┌──────────────┐
       │  PostgreSQL  │      │    Redis     │
       └──────────────┘      └──────────────┘

                LangSmith ← tracing & evals
```

Nodes are deterministic given the same state. Interrupts are explicit (`interrupt_before=["approve_risk","approve_final"]`) and every transition is persisted.

---

## Quickstart (Terminal)

### 1. Prerequisites

```bash
# Required
python --version          # 3.11+
docker --version          # 24+
docker compose version    # v2+
git --version

# Optional but recommended
psql --version            # for poking at the DB
```

### 2. Clone & enter the project

```bash
git clone https://github.com/AshraHossain/langgraph-hitl-agent.git
cd langgraph-hitl-agent
```

### 3. Create a virtual environment and install

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### 4. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in:
#   OPENAI_API_KEY=sk-...
#   LANGCHAIN_API_KEY=lsv2_...      (LangSmith; optional)
#   LANGCHAIN_TRACING_V2=true
#   LANGCHAIN_PROJECT=langgraph-hitl-agent
```

### 5. Start Postgres + Redis

```bash
docker compose up -d postgres redis
docker compose ps
```

### 6. Run migrations

```bash
python -m app.database migrate
```

### 7. Start the API

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# Open http://localhost:8000/docs
```

### 8. Exercise the pipeline

```bash
# Submit an RFP (returns a thread_id)
curl -sS -X POST http://localhost:8000/rfp \
  -H "Content-Type: application/json" \
  -d '{"title":"ACME Cloud Migration RFP","content":"Migrate 40 services to AWS. Budget 2M. Deadline Q4."}' | tee /tmp/resp.json

THREAD_ID=$(jq -r .thread_id /tmp/resp.json)
echo "Thread: $THREAD_ID"

# The graph will pause at approve_risk. Inspect:
curl -sS http://localhost:8000/threads/$THREAD_ID | jq

# Approve the risk assessment
curl -sS -X POST http://localhost:8000/approvals/$THREAD_ID \
  -H "Content-Type: application/json" \
  -d '{"gate":"approve_risk","decision":"approve","reviewer":"ashraf","notes":"looks good"}'

# Graph resumes, then pauses at approve_final. Approve again:
curl -sS -X POST http://localhost:8000/approvals/$THREAD_ID \
  -H "Content-Type: application/json" \
  -d '{"gate":"approve_final","decision":"approve","reviewer":"ashraf","notes":"ship it"}'

# Fetch the final result and audit trail
curl -sS http://localhost:8000/threads/$THREAD_ID | jq
curl -sS http://localhost:8000/audit/$THREAD_ID | jq
```

### 9. Run tests

```bash
pytest -v                              # unit + integration
pytest -v -m "not integration"         # unit only (no DB)
pytest --cov=app --cov-report=term-missing
```

### 10. Spin everything up with Docker (optional)

```bash
docker compose up --build
# API on :8000, Postgres on :5432, Redis on :6379
```

---

## Project layout

```
langgraph-hitl-agent/
├── app/
│   ├── main.py                # FastAPI entrypoint + lifespan
│   ├── config.py              # Pydantic Settings
│   ├── database.py            # asyncpg pool + migrations
│   ├── redis_client.py        # pub/sub wrapper
│   ├── models/
│   │   ├── schemas.py         # API request/response models
│   │   └── audit.py           # AuditEvent dataclasses
│   ├── graph/
│   │   ├── state.py           # RFPState TypedDict
│   │   ├── nodes.py           # ingest / extract / analyze / compose / finalize
│   │   ├── builder.py         # build_graph() with HITL interrupts
│   │   └── checkpointer.py    # AsyncPostgresSaver wiring
│   ├── api/
│   │   ├── rfp.py             # POST /rfp, GET /threads/{id}
│   │   ├── approvals.py       # POST /approvals/{id}
│   │   └── audit.py           # GET /audit/{id}
│   ├── services/
│   │   ├── llm.py             # LLM wrapper with retry budget
│   │   ├── retry.py           # RetryBudget (token-bucket)
│   │   └── observability.py   # LangSmith helpers
│   └── utils/logger.py
├── migrations/001_initial.sql
├── tests/
│   ├── conftest.py
│   ├── test_graph_unit.py
│   ├── test_retry_budget.py
│   ├── test_api.py
│   └── test_integration.py
├── .github/workflows/ci.yml
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml
├── Makefile
├── .env.example
├── .gitignore
└── README.md
```

---

## DO-178C-inspired discipline

| Practice                        | How it shows up                                                  |
|---------------------------------|------------------------------------------------------------------|
| Deterministic state machine     | Pure node functions, explicit reducer, no hidden global state    |
| Requirements traceability       | Every node has a docstring tag `REQ-<id>`; `/audit/{id}` maps events to requirements |
| Human approval gates            | `interrupt_before` at `approve_risk` and `approve_final`         |
| Fault tolerance                 | Postgres checkpointer — resume from any committed step           |
| Bounded retries                 | `RetryBudget` token-bucket per thread; exceeded → `FAILED`       |
| Structural coverage             | `pytest --cov` gate in CI, target ≥ 85%                          |
| Independent verification        | Integration tests exercise full resume path with fresh process   |

---

## API reference

- `POST /rfp` → `{ thread_id, status }`
- `GET  /threads/{thread_id}` → current state + next pending gate
- `POST /approvals/{thread_id}` → `{ gate, decision: approve|reject|revise, reviewer, notes }`
- `GET  /audit/{thread_id}` → ordered list of `AuditEvent`

See `/docs` (Swagger UI) when the server is running.

---

## License

MIT
