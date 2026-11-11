-- p8-memory-observability: long-term memory, separate from LangGraph checkpoints (TRD §16; spec Behaviour §1, Data).
-- Two kinds only: a user's preference (an allowed key and value from configs/agent.yaml memory.preferences) and a
-- reviewed, sanitized troubleshooting pattern shared with the team. Never credentials, personal or merchant data,
-- or unverified inferences. Read and written by devguard_app (no DELETE: user deletes are soft deletes that blank
-- the text); hard-deleted after expiry or soft delete by the owner (scripts/purge_retention.py).
-- Grants are applied by app/db/roles.py, not here (role names are parameterised).

CREATE TABLE long_term_memory (
    memory_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id           text NOT NULL CHECK (owner_id ~ '^[A-Za-z0-9._@-]{1,64}$'),
    scope              text NOT NULL CHECK (scope IN ('user', 'team')),
    memory_type        text NOT NULL CHECK (memory_type IN ('user_preference', 'verified_pattern')),
    pref_key           text CHECK (pref_key IS NULL OR pref_key ~ '^[a-z_]{1,32}$'),
    title              text NOT NULL DEFAULT '' CHECK (char_length(title) <= 120),
    content_sanitized  text NOT NULL CHECK (char_length(content_sanitized) <= 1000),
    api_family         text CHECK (api_family IS NULL OR api_family ~ '^[a-z0-9_]{1,64}$'),
    intent             text CHECK (intent IS NULL OR intent ~ '^[a-z_]{1,32}$'),
    status             text NOT NULL CHECK (status IN ('active', 'pending', 'rejected')),
    confidence         text NOT NULL CHECK (confidence IN ('explicit', 'verified')),
    proposed_by        text NOT NULL CHECK (proposed_by ~ '^[A-Za-z0-9._@-]{1,64}$'),
    approved_by        text CHECK (approved_by IS NULL OR approved_by ~ '^[A-Za-z0-9._@-]{1,64}$'),
    decision_reason    text CHECK (decision_reason IS NULL OR char_length(decision_reason) <= 500),
    source_thread_id   text CHECK (source_thread_id IS NULL OR source_thread_id ~ '^[A-Za-z0-9_-]{16,64}$'),
    source_run_id      uuid,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    expires_at         timestamptz NOT NULL,
    deleted_at         timestamptz,
    version            integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    -- four eyes: whoever approves a pattern did not propose it (the API checks this too)
    CONSTRAINT memory_four_eyes CHECK (approved_by IS NULL OR approved_by <> proposed_by),
    -- a preference is the user's own, explicit and keyed; a pattern is team-scoped, reviewed and unkeyed
    CONSTRAINT memory_shape CHECK (
        (memory_type = 'user_preference' AND scope = 'user' AND pref_key IS NOT NULL AND confidence = 'explicit'
             AND status = 'active' AND owner_id = proposed_by)
     OR (memory_type = 'verified_pattern' AND scope = 'team' AND pref_key IS NULL AND confidence = 'verified')),
    -- an active pattern always names its reviewer
    CONSTRAINT memory_pattern_reviewed CHECK (memory_type <> 'verified_pattern' OR status <> 'active'
                                              OR approved_by IS NOT NULL)
);
-- one live preference per user and key; a deleted one may be saved again as a new row
CREATE UNIQUE INDEX memory_one_preference ON long_term_memory (owner_id, pref_key)
    WHERE memory_type = 'user_preference' AND deleted_at IS NULL;
CREATE INDEX memory_by_owner ON long_term_memory (owner_id);
CREATE INDEX memory_patterns_live ON long_term_memory (status, expires_at) WHERE memory_type = 'verified_pattern';

-- Version history (TRD §16.2 "keep version history"): the content a row held before each update.
-- Blanked together with its memory on delete; removed with it by the retention job (ON DELETE CASCADE).
CREATE TABLE long_term_memory_history (
    memory_id          uuid NOT NULL REFERENCES long_term_memory (memory_id) ON DELETE CASCADE,
    version            integer NOT NULL CHECK (version >= 1),
    content_sanitized  text NOT NULL CHECK (char_length(content_sanitized) <= 1000),
    changed_by         text NOT NULL CHECK (changed_by ~ '^[A-Za-z0-9._@-]{1,64}$'),
    changed_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (memory_id, version)
);
