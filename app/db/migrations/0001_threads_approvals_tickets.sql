-- p6-checkpoint-hitl: threads, human approvals and the only side effect (local sandbox tickets).
-- Every column holds sanitized synthetic text, IDs or hashes only (TRD §17; CLAUDE.md "no secrets anywhere").
-- LangGraph's checkpoint tables are created separately by AsyncPostgresSaver.setup().

CREATE TABLE agent_threads (
    thread_id    text PRIMARY KEY CHECK (thread_id ~ '^[A-Za-z0-9_-]{16,64}$'),
    owner_id     text NOT NULL,
    status       text NOT NULL DEFAULT 'idle'
                 CHECK (status IN ('idle', 'running', 'awaiting_approval', 'completed', 'failed')),
    last_run_id  uuid,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX agent_threads_owner ON agent_threads (owner_id);

CREATE TABLE approval_requests (
    approval_id      uuid PRIMARY KEY,
    thread_id        text NOT NULL REFERENCES agent_threads (thread_id),
    run_id           uuid NOT NULL,
    requester_id     text NOT NULL,
    requester_role   text NOT NULL,
    action_type      text NOT NULL CHECK (action_type = 'create_support_ticket'),
    action_payload   jsonb NOT NULL,
    action_hash      char(64) NOT NULL,
    status           text NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'executed')),
    approver_id      text,
    decision_reason  text CHECK (decision_reason IS NULL OR char_length(decision_reason) <= 500),
    decided_at       timestamptz,
    expires_at       timestamptz NOT NULL,
    executed_at      timestamptz,
    ticket_id        uuid,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT approval_four_eyes CHECK (approver_id IS NULL OR approver_id <> requester_id)
);
-- One pending approval per exact action on a thread: re-running request_human_approval on resume is idempotent.
CREATE UNIQUE INDEX approval_one_pending ON approval_requests (thread_id, action_hash) WHERE status = 'pending';
CREATE INDEX approval_by_thread ON approval_requests (thread_id);

CREATE TABLE sandbox_tickets (
    ticket_id        uuid PRIMARY KEY,
    idempotency_key  char(64) NOT NULL,
    approval_id      uuid NOT NULL REFERENCES approval_requests (approval_id),
    thread_id        text NOT NULL REFERENCES agent_threads (thread_id),
    requester_id     text NOT NULL,
    title            text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
    body_sanitized   text NOT NULL CHECK (char_length(body_sanitized) BETWEEN 1 AND 4000),
    evidence_ids     jsonb NOT NULL,
    priority         text NOT NULL CHECK (priority IN ('low', 'medium', 'high')),
    status           text NOT NULL DEFAULT 'open' CHECK (status = 'open'),
    created_at       timestamptz NOT NULL DEFAULT now(),
    -- The idempotency boundary (TRD §11.4): one ticket per (thread, action, normalized payload), enforced here.
    CONSTRAINT sandbox_tickets_idempotency_key UNIQUE (idempotency_key)
);
