import { afterEach, expect, test, vi } from "vitest";
import type { NextRequest } from "next/server";
import { POST } from "@/app/api/webhooks/clerk/route";

const mocks = vi.hoisted(() => ({
  verifyWebhook: vi.fn(),
  getDatabasePool: vi.fn(),
}));

vi.mock("@clerk/nextjs/webhooks", () => ({ verifyWebhook: mocks.verifyWebhook }));
vi.mock("@/lib/database", () => ({ getDatabasePool: mocks.getDatabasePool }));

const previousSecret = process.env.CLERK_WEBHOOK_SIGNING_SECRET;

afterEach(() => {
  vi.clearAllMocks();
  if (previousSecret === undefined) delete process.env.CLERK_WEBHOOK_SIGNING_SECRET;
  else process.env.CLERK_WEBHOOK_SIGNING_SECRET = previousSecret;
});

function request() {
  return new Request("http://localhost:3000/api/webhooks/clerk", {
    method: "POST",
    headers: { "svix-id": "event-1" },
  }) as NextRequest;
}

test("requires a signing secret and rejects an invalid signature", async () => {
  delete process.env.CLERK_WEBHOOK_SIGNING_SECRET;
  expect((await POST(request())).status).toBe(503);

  process.env.CLERK_WEBHOOK_SIGNING_SECRET = "whsec_test";
  mocks.verifyWebhook.mockRejectedValue(new Error("Invalid signature"));
  expect((await POST(request())).status).toBe(400);
  expect(mocks.getDatabasePool).not.toHaveBeenCalled();
});

test("acknowledges unrelated Clerk events without touching the database", async () => {
  process.env.CLERK_WEBHOOK_SIGNING_SECRET = "whsec_test";
  mocks.verifyWebhook.mockResolvedValue({ type: "session.created", data: {} });
  const response = await POST(request());
  expect(response.status).toBe(200);
  await expect(response.json()).resolves.toEqual({ received: true, ignored: true });
  expect(mocks.getDatabasePool).not.toHaveBeenCalled();
});
