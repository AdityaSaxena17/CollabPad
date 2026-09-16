BEGIN;

CREATE TABLE IF NOT EXISTS users (
  clerk_id TEXT PRIMARY KEY,
  username TEXT,
  primary_email TEXT,
  first_name TEXT,
  last_name TEXT,
  image_url TEXT,
  clerk_created_at TIMESTAMPTZ,
  clerk_updated_at TIMESTAMPTZ,
  deleted_at TIMESTAMPTZ,
  last_event_type TEXT NOT NULL,
  synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS users_primary_email_idx
  ON users (primary_email)
  WHERE primary_email IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS users_username_idx
  ON users (username)
  WHERE username IS NOT NULL AND deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS clerk_webhook_events (
  id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE users IS
  'Eventually consistent local mirror of Clerk users; Clerk remains the identity source of truth.';

COMMENT ON TABLE clerk_webhook_events IS
  'Processed Clerk webhook message IDs used to make retries idempotent.';

COMMIT;
