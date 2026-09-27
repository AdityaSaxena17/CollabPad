# Collab Docs

Collab Docs is the Next.js client and Python collaboration prototype for option C in the AOS 2026–27 assignment. Signed-in users can create documents, enable a link for other signed-in editors, and edit together with durable updates. The Python gateway and collaboration service communicate through gRPC.

## Run locally

Requirements: Node.js 20.9+, Docker Desktop, and a Clerk application.

1. Run `npm install` in `my-app`.
2. Copy the values in `.env.example` into your local `.env` and fill in your Clerk publishable key, secret key, and webhook signing secret. Keep `.env` out of Git. The default frontend origin is `http://localhost:3000`.
3. Start the database and Python services:

   ```bash
   docker compose up --build -d
   docker compose ps -a
   ```

   The `migrate` job should exit with code 0. It runs the versioned collaboration migration on both fresh and existing PostgreSQL volumes. Existing Clerk user tables are initialized by `db/migrations/001_clerk_users.sql` when PostgreSQL first creates its volume.

4. Start Next.js on port 3000:

   ```bash
   npm run dev
   ```

5. Open `http://localhost:3000/documents`. Create a blank document, open Share, and enable the signed-in link. Open that link in another browser profile signed in as a second Clerk user. Both users can edit the same content at once; a third user can join the same link.

Docker PostgreSQL is published on `localhost:55432`, leaving a locally installed PostgreSQL on `5432` alone. `COLLAB_DOCS_POSTGRES_PORT` changes the host mapping if necessary. The gateway is published on `localhost:18080` by default through `GATEWAY_PORT`. The collaboration gRPC port is private to Compose. If you change the gateway host port, update both `NEXT_PUBLIC_GATEWAY_HTTP_URL` and `NEXT_PUBLIC_GATEWAY_WS_URL` in `.env` and restart Next.js. The gateway accepts only `FRONTEND_ORIGIN`; keep it aligned with the URL used to open Next.js.

## How editing works

The browser uses a WebSocket to the Python gateway. The gateway verifies the Clerk session token and opens a bidirectional gRPC stream to the collaboration service. Tiptap generates Yjs updates; the browser merges nearby updates after 50 ms of inactivity, at most 200 ms after the first update, or when the batch reaches 128 KiB. The collaboration service validates access, commits each merged update to PostgreSQL, then acknowledges and broadcasts it. The editor says **Saved** only after acknowledgement, including any buffered edits. On reconnect, it merges the saved snapshot and resends unacknowledged updates still held in the open browser tab, then flushes its current buffer.

Documents start private. The owner can enable or disable editing by any signed-in person with the document URL. Disabling sharing closes active guest streams. The dashboard lists documents owned by the signed-in user; a shared document opens through its link. The old static sample documents have been removed because they were never persisted.

The PostgreSQL `collaboration` schema stores document metadata, an operation log of Yjs updates, and periodic snapshots. Alembic migrations run through the `migrate` Compose job. The single collaboration replica serializes writes per document; adding multiple collaboration replicas requires the later Raft design. The existing `users` and `clerk_webhook_events` tables remain separate and Clerk stays the source of truth for identity. Document ownership uses the verified Clerk user ID, so a delayed webhook does not block a newly signed-in user from creating a document.

Editors become read-only while disconnected. A warning appears when leaving with unacknowledged changes; a full browser refresh before acknowledgement can lose those local changes. AI assistance, version history, and Raft are not implemented in this slice.

## Presence and live carets

Redis runs privately in Compose; it is not involved in document saves. Each open editor socket has a 45-second Redis presence key, refreshed every 15 seconds while the gateway keeps that socket alive. Clean closes remove the key immediately. Redis Pub/Sub announces joins, leaves, and cursor updates; key-expiry notifications announce crashed connections. A document-scoped index helps find keys, but snapshots check that every presence key still exists and discard stale index entries. These are the only two Redis key types used for presence; Yjs client IDs are not separately reserved. Gateways subscribe before resynchronizing on Redis reconnection, and re-register their still-open sockets after Redis recovery. There is no periodic Redis presence poll.

The document header shows other signed-in editors once per Clerk user; separate tabs retain separate live carets. Names and profile images come from the gateway's Clerk lookup, not from browser claims. JOIN and cursor notifications read one live connection key and send an upsert; LEAVE and expiry notifications send a removal only after confirming that key is gone. Full snapshots are sent on document join and subscriber recovery. When Redis is unavailable, the editor reports presence as unavailable and clears remote carets, while document editing and PostgreSQL saves continue. Redis Pub/Sub and expiry notifications are best-effort, so a missed notification can leave stale presence visible until a full snapshot on rejoin or recovery.

## Clerk webhook

The public `POST /api/webhooks/clerk` route verifies Clerk's signature and processes `user.created`, `user.updated`, and `user.deleted`. In Clerk Dashboard, create a webhook endpoint at `https://<your-public-host>/api/webhooks/clerk`, subscribe to those three events, and put its signing secret in `CLERK_WEBHOOK_SIGNING_SECRET`. For local testing, expose Next.js through a secure tunnel. Existing Clerk users require event replay or a separate backfill; webhooks do not automatically sync past users.

## Checks

```bash
npm run lint
npx tsc --noEmit
npm run build
docker compose config --quiet
docker compose exec -T collaboration python -m unittest services.tests.test_collaboration services.tests.test_gateway -v
docker compose exec -T collaboration python -m unittest services.tests.test_presence -v
```

The Python tests use synthetic identities and remove their test documents. Real Clerk sign-in, OAuth, and two-browser interaction should also be checked in the configured Clerk application. The gateway rejects unsigned or expired sessions; there is no development authentication bypass.
