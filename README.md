# Collab Docs Frontend

Collab Docs is the Next.js client for the distributed real-time collaboration platform described in the AOS 2026–27 assignment. This milestone provides Clerk authentication, a document dashboard, and a functional local rich-text editor. The future gRPC, LLM, and Raft services remain Python-only.

## Requirements

- Node.js 20.9 or newer
- A Clerk development application

## Setup

1. Install dependencies:

   ```bash
   npm install
   ```

2. Add the missing values from `.env.example` to your local environment file. Keep your Clerk keys, database password, and webhook signing secret out of Git.

3. Start PostgreSQL. The first startup automatically applies the SQL files in `db/migrations`:

   ```bash
   docker compose up -d postgres
   ```

   Docker publishes PostgreSQL on `localhost:55432` by default so it does not conflict with a local PostgreSQL server on `5432`. Set `COLLAB_DOCS_POSTGRES_PORT` to another free host port if needed, and keep `DATABASE_URL` in sync.

4. Start the development server:

   ```bash
   npm run dev
   ```

5. Open [http://localhost:3000](http://localhost:3000). Clerk redirects signed-out users to `/sign-in`; successful sign-in and sign-up flows return to `/documents`.

The host-side Next.js process connects with the `DATABASE_URL` shown in `.env.example`. Future services running inside this Compose project should use `postgres:5432` instead of the host-side address.

## Clerk user synchronization

The public `POST /api/webhooks/clerk` route verifies Clerk's webhook signature before touching the database. It processes `user.created`, `user.updated`, and `user.deleted` events.

1. Expose the local app with a tunnel such as ngrok or Cloudflare Tunnel when testing locally.
2. In the Clerk Dashboard, open **Webhooks**, add an endpoint at `https://<your-public-host>/api/webhooks/clerk`, and subscribe to:
   - `user.created`
   - `user.updated`
   - `user.deleted`
3. Copy that endpoint's signing secret into `CLERK_WEBHOOK_SIGNING_SECRET` in your local environment file.
4. Send Clerk's test events and confirm they return HTTP 200.

The webhook uses Svix message IDs plus PostgreSQL upserts, so repeated deliveries are safe. User updates older than the stored Clerk timestamp are ignored. Deletion is terminal for a Clerk user ID: personal fields are cleared and a tombstone is retained so a late update cannot recreate a deleted user.

Webhook delivery is eventually consistent. Users that existed before the endpoint was created are not automatically backfilled; replay their events in Clerk or add an explicit Backend API backfill before relying on a complete user directory. See Clerk's [user synchronization guide](https://clerk.com/docs/guides/development/webhooks/syncing).

### Database tables

- `users`: the minimal, queryable Clerk user mirror keyed by `clerk_id`.
- `clerk_webhook_events`: processed webhook IDs for retry idempotency; payloads are deliberately not stored.

Clerk remains the source of truth for identity. Application-owned profile fields should be added separately rather than written back into mirrored Clerk columns.

## Current routes

- `/sign-in` and `/sign-up`: public Clerk authentication flows.
- `/documents`: protected, searchable local document dashboard.
- `/documents/new`: protected blank draft.
- `/documents/[documentId]`: protected sample document editor.

## Current behavior

The editor supports headings, fonts, sizes, marks, colours, highlighting, links, alignment, lists, indentation, horizontal rules, undo/redo, zoom, word counts, and printing. Content uses Tiptap JSON in memory.

Document persistence, remote presence, sharing, AI assistance, and version history are intentionally disabled until their backend services exist. The UI labels this state and never presents mock server results as real data. Refreshing an editor restores its original sample content.

## Backend integration boundary

Server pages own authentication and initial document lookup. The interactive editor is isolated in a client component and receives a small presentation model containing a document ID, title, update label, and Tiptap JSON. A later transport adapter can connect document loading, updates, presence, AI actions, and history without coupling UI components to individual microservices.

No REST, gRPC-Web, WebSocket, Yjs, or Hocuspocus transport is selected in this milestone.

## Verification

```bash
npm run lint
npx tsc --noEmit
npm run build
```

Real sign-in and session behavior must be smoke-tested after valid Clerk keys are present.

Validate the Compose file and inspect database health with:

```bash
docker compose config
docker compose ps
```
