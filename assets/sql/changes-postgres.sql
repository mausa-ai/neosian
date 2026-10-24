-- Optional change-feed extension 1 for reference schema 4.
-- Pause every writer, apply as the schema owner, then restart with compatible
-- binaries. The core detects this extension and never installs it on connection.
BEGIN;
CREATE TABLE IF NOT EXISTS {{schema}}.neosian_changes (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    version integer NOT NULL
);
INSERT INTO {{schema}}.neosian_changes (version) VALUES (1)
    ON CONFLICT (singleton) DO NOTHING;
CREATE TABLE IF NOT EXISTS {{schema}}.memory_change_streams (
    scope text PRIMARY KEY,
    format integer NOT NULL CHECK (format = 1),
    stream text UNIQUE NOT NULL,
    sequence bigint NOT NULL CHECK (sequence >= 0),
    floor bigint NOT NULL CHECK (floor >= 0 AND floor <= sequence)
);
CREATE TABLE IF NOT EXISTS {{schema}}.memory_changes (
    scope text NOT NULL REFERENCES {{schema}}.memory_change_streams (scope)
        DEFERRABLE INITIALLY DEFERRED,
    sequence bigint NOT NULL CHECK (sequence > 0),
    event jsonb NOT NULL,
    PRIMARY KEY (scope, sequence)
);
COMMIT;
