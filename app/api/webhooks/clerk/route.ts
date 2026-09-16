import type { UserJSON } from "@clerk/nextjs/server";
import { verifyWebhook } from "@clerk/nextjs/webhooks";
import type { NextRequest } from "next/server";
import type { Pool } from "pg";
import { getDatabasePool } from "@/lib/database";

export const runtime = "nodejs";

async function upsertUser(
  database: Pool,
  messageId: string,
  eventType: "user.created" | "user.updated",
  user: UserJSON,
) {
  const primaryEmail =
    user.email_addresses.find(
      (emailAddress) => emailAddress.id === user.primary_email_address_id,
    )?.email_address ?? null;

  await database.query(
    `
      WITH accepted_event AS (
        INSERT INTO clerk_webhook_events (id, event_type)
        VALUES ($1, $2)
        ON CONFLICT (id) DO NOTHING
        RETURNING id
      )
      INSERT INTO users (
        clerk_id,
        username,
        primary_email,
        first_name,
        last_name,
        image_url,
        clerk_created_at,
        clerk_updated_at,
        deleted_at,
        last_event_type,
        synced_at
      )
      SELECT
        $3,
        $4,
        $5,
        $6,
        $7,
        $8,
        $9,
        $10,
        NULL,
        $2,
        NOW()
      FROM accepted_event
      ON CONFLICT (clerk_id) DO UPDATE SET
        username = EXCLUDED.username,
        primary_email = EXCLUDED.primary_email,
        first_name = EXCLUDED.first_name,
        last_name = EXCLUDED.last_name,
        image_url = EXCLUDED.image_url,
        clerk_created_at = EXCLUDED.clerk_created_at,
        clerk_updated_at = EXCLUDED.clerk_updated_at,
        last_event_type = EXCLUDED.last_event_type,
        synced_at = NOW()
      WHERE
        users.deleted_at IS NULL
        AND (
          users.clerk_updated_at IS NULL
          OR EXCLUDED.clerk_updated_at >= users.clerk_updated_at
        )
    `,
    [
      messageId,
      eventType,
      user.id,
      user.username,
      primaryEmail,
      user.first_name,
      user.last_name,
      user.image_url,
      new Date(user.created_at),
      new Date(user.updated_at),
    ],
  );
}

async function markUserDeleted(database: Pool, messageId: string, clerkId: string) {
  await database.query(
    `
      WITH accepted_event AS (
        INSERT INTO clerk_webhook_events (id, event_type)
        VALUES ($1, 'user.deleted')
        ON CONFLICT (id) DO NOTHING
        RETURNING id
      )
      INSERT INTO users (
        clerk_id,
        deleted_at,
        last_event_type,
        synced_at
      )
      SELECT
        $2,
        NOW(),
        'user.deleted',
        NOW()
      FROM accepted_event
      ON CONFLICT (clerk_id) DO UPDATE SET
        username = NULL,
        primary_email = NULL,
        first_name = NULL,
        last_name = NULL,
        image_url = NULL,
        deleted_at = NOW(),
        last_event_type = 'user.deleted',
        synced_at = NOW()
    `,
    [messageId, clerkId],
  );
}

/** Verifies and applies Clerk user lifecycle events to the PostgreSQL mirror. */
export async function POST(request: NextRequest) {
  if (!process.env.CLERK_WEBHOOK_SIGNING_SECRET) {
    return Response.json(
      { error: "Webhook signing secret is not configured." },
      { status: 503 },
    );
  }

  let event;

  try {
    event = await verifyWebhook(request);
  } catch {
    return Response.json({ error: "Invalid webhook signature." }, { status: 400 });
  }

  if (
    event.type !== "user.created" &&
    event.type !== "user.updated" &&
    event.type !== "user.deleted"
  ) {
    return Response.json({ received: true, ignored: true });
  }

  const messageId = request.headers.get("svix-id")?.trim();

  if (!messageId) {
    return Response.json({ error: "Webhook message ID is missing." }, { status: 400 });
  }

  try {
    const database = getDatabasePool();

    if (event.type === "user.deleted") {
      if (!event.data.id) {
        return Response.json({ error: "Deleted user ID is missing." }, { status: 400 });
      }

      await markUserDeleted(database, messageId, event.data.id);
    } else {
      await upsertUser(database, messageId, event.type, event.data);
    }

    return Response.json({ received: true });
  } catch {
    console.error("Failed to synchronize a verified Clerk user event.");
    return Response.json({ error: "User synchronization failed." }, { status: 500 });
  }
}
