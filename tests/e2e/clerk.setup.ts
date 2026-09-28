import { clerkSetup } from "@clerk/testing/playwright";
import { test } from "@playwright/test";

test("prepare Clerk testing token", async () => {
  await clerkSetup();
});
