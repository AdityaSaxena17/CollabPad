import { Pool } from "pg";
import type { NextRequest } from "next/server";
import { afterAll, beforeAll, expect, test, vi } from "vitest";
import { POST } from "@/app/api/webhooks/clerk/route";
import { getDatabasePool } from "@/lib/database";

const mocks = vi.hoisted(() => ({ verifyWebhook: vi.fn() }));
vi.mock("@clerk/nextjs/webhooks", () => ({ verifyWebhook: mocks.verifyWebhook }));

const userId = `test_webhook_${crypto.randomUUID()}`;
const eventIds: string[] = [];
const database = new Pool({ connectionString: process.env.DATABASE_URL });
const previousSecret = process.env.CLERK_WEBHOOK_SIGNING_SECRET;

beforeAll(() => {
  process.env.CLERK_WEBHOOK_SIGNING_SECRET = "whsec_test";
});

afterAll(async () => {
  await database.query("DELETE FROM users WHERE clerk_id = $1", [userId]);
  await database.query("DELETE FROM clerk_webhook_events WHERE id = ANY($1)", [eventIds]);
  await database.end();
  await getDatabasePool().end();
  if (previousSecret === undefined) delete process.env.CLERK_WEBHOOK_SIGNING_SECRET;
  else process.env.CLERK_WEBHOOK_SIGNING_SECRET = previousSecret;
});

function request(messageId: string): NextRequest {
  eventIds.push(messageId);
  return new Request("http://localhost:3001/api/webhooks/clerk", {
    method: "POST",
    headers: { "svix-id": messageId },
  }) as NextRequest;
}

function user(updatedAt: number, username: string) {
  return {
    id: userId,
    username,
    primary_email_address_id: "email-1",
    email_addresses: [{ id: "email-1", email_address: "webhook-test@example.com" }],
    first_name: "Test",
    last_name: "User",
    image_url: "https://example.com/avatar.png",
    created_at: updatedAt - 1_000,
    updated_at: updatedAt,
  };
}

test("deduplicates events, ignores older updates, and records deletion", async () => {
  const baseline = Date.now();
  const createdId = `event_${crypto.randomUUID()}`;
  mocks.verifyWebhook.mockResolvedValue({ type: "user.created", data: user(baseline, "first") });
  expect((await POST(request(createdId))).status).toBe(200);

  mocks.verifyWebhook.mockResolvedValue({ type: "user.updated", data: user(baseline + 1_000, "duplicate") });
  expect((await POST(request(createdId))).status).toBe(200);
  let record = await database.query("SELECT username FROM users WHERE clerk_id = $1", [userId]);
  expect(record.rows[0].username).toBe("first");

  mocks.verifyWebhook.mockResolvedValue({ type: "user.updated", data: user(baseline + 2_000, "newer") });
  expect((await POST(request(`event_${crypto.randomUUID()}`))).status).toBe(200);
  mocks.verifyWebhook.mockResolvedValue({ type: "user.updated", data: user(baseline + 500, "older") });
  expect((await POST(request(`event_${crypto.randomUUID()}`))).status).toBe(200);
  record = await database.query("SELECT username FROM users WHERE clerk_id = $1", [userId]);
  expect(record.rows[0].username).toBe("newer");

  mocks.verifyWebhook.mockResolvedValue({ type: "user.deleted", data: { id: userId } });
  expect((await POST(request(`event_${crypto.randomUUID()}`))).status).toBe(200);
  record = await database.query("SELECT username, deleted_at FROM users WHERE clerk_id = $1", [userId]);
  expect(record.rows[0].username).toBeNull();
  expect(record.rows[0].deleted_at).not.toBeNull();
});
