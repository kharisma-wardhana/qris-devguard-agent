-- p7-mcp-hardening: MCP tool-call audit trail (TRD §13.4 Auditability; spec Data).
-- No column can hold a tool argument, message, payload, token or free text: identifiers and codes only.
-- Written by the insert-only devguard_audit role; purged after audit.retention_days by the owner
-- (scripts/purge_audit.py). Grants are applied by app/db/roles.py, not here (role names are parameterised).

CREATE TABLE tool_audit_logs (
    audit_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    correlation_id  char(32) NOT NULL CHECK (correlation_id ~ '^[0-9a-f]{32}$'),
    actor_sub       text NOT NULL CHECK (actor_sub ~ '^([A-Za-z0-9._@-]{1,64}|-)$'),
    actor_role      text NOT NULL
                    CHECK (actor_role IN ('-', 'developer', 'support_engineer', 'kb_maintainer', 'admin')),
    tool            text CHECK (tool IS NULL OR tool ~ '^[a-z_]{1,64}$'),
    phase           text NOT NULL CHECK (phase IN ('attempt', 'result', 'refused')),
    outcome         text NOT NULL CHECK (outcome IN (
                        'ATTEMPT', 'OK', 'UNAUTHENTICATED', 'FORBIDDEN', 'INVALID_ARGUMENT', 'RATE_LIMITED',
                        'PAYLOAD_TOO_LARGE', 'SEARCH_UNAVAILABLE', 'INTERNAL_ERROR', 'TICKET_STORE_UNAVAILABLE',
                        'APPROVAL_INVALID', 'APPROVAL_MISMATCH', 'APPROVAL_EXPIRED', 'APPROVAL_NOT_USABLE',
                        'AUDIT_UNAVAILABLE')),
    duration_ms     integer CHECK (duration_ms IS NULL OR duration_ms >= 0),
    run_id          uuid,
    approval_id     uuid,
    -- a refusal before any tool ran has no tool; a tool phase always names one
    CONSTRAINT tool_audit_phase_tool CHECK ((phase = 'refused') = (tool IS NULL))
);
CREATE INDEX tool_audit_logs_time ON tool_audit_logs (occurred_at);
CREATE INDEX tool_audit_logs_correlation ON tool_audit_logs (correlation_id);
