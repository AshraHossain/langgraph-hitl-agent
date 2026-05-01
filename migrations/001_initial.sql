-- LangGraph's Postgres checkpointer manages its own tables (`checkpoints`,
-- `checkpoint_writes`, `checkpoint_blobs`). They are created automatically by
-- AsyncPostgresSaver.setup(). This migration sets up everything else we own.

CREATE TABLE IF NOT EXISTS rfp_thread (
    thread_id      TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    content        TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'RUNNING',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rfp_thread_status ON rfp_thread(status);

-- Append-only audit log. Every node entry/exit, approval, and error lands here.
CREATE TABLE IF NOT EXISTS audit_event (
    id             BIGSERIAL PRIMARY KEY,
    thread_id      TEXT NOT NULL REFERENCES rfp_thread(thread_id) ON DELETE CASCADE,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_type     TEXT NOT NULL,       -- e.g. NODE_ENTER, NODE_EXIT, APPROVAL, ERROR, RETRY
    node           TEXT,                -- nullable (for non-node events)
    requirement    TEXT,                -- DO-178C-style requirement tag (REQ-...)
    actor          TEXT,                -- system | <reviewer>
    payload        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_audit_event_thread_ts ON audit_event(thread_id, ts);
CREATE INDEX IF NOT EXISTS idx_audit_event_type     ON audit_event(event_type);

-- Per-thread retry budget counters. Token-bucket refill is computed in code.
CREATE TABLE IF NOT EXISTS retry_budget (
    thread_id      TEXT PRIMARY KEY REFERENCES rfp_thread(thread_id) ON DELETE CASCADE,
    tokens         INTEGER NOT NULL,
    last_refill_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Lightweight view: latest pending interrupt gate per thread
CREATE OR REPLACE VIEW v_pending_gate AS
SELECT DISTINCT ON (thread_id)
       thread_id,
       payload->>'gate' AS gate,
       ts
FROM   audit_event
WHERE  event_type = 'INTERRUPT'
ORDER  BY thread_id, ts DESC;
