import { clerk } from "@clerk/testing/playwright";
import { expect, test } from "@playwright/test";

test("completion stays private until accepted into the shared document", async ({ browser }) => {
  const ownerContext = await browser.newContext();
  const guestContext = await browser.newContext();
  try {
    const owner = await ownerContext.newPage();
    const guest = await guestContext.newPage();
    await owner.goto("/sign-in");
    await clerk.signIn({ page: owner, emailAddress: process.env.E2E_OWNER_EMAIL! });
    await owner.goto("/documents/new");
    await owner.getByRole("button", { name: "Create document" }).click();
    const documentUrl = owner.url();
    await owner.getByRole("button", { name: "Share" }).click();
    await owner.getByRole("button", { name: "Allow signed-in editors with link" }).click();
    await owner.getByRole("button", { name: "Close sharing" }).click();

    await guest.goto("/sign-in");
    await clerk.signIn({ page: guest, emailAddress: process.env.E2E_GUEST_EMAIL! });
    await guest.goto(documentUrl);
    const ownerEditor = owner.locator('[contenteditable="true"][aria-label="Document content"]');
    const guestEditor = guest.locator('[contenteditable="true"][aria-label="Document content"]');
    await expect(ownerEditor).toBeVisible();
    await expect(guestEditor).toBeVisible();

    await ownerEditor.fill("Hello");
    await expect(owner.locator(".ai-completion-ghost")).toContainText("suggestion");
    await expect(guestEditor).toContainText("Hello");
    await expect(guestEditor).not.toContainText("suggestion");
    await owner.getByRole("button", { name: "Accept word" }).click();
    await expect(guestEditor).toContainText("Hello suggestion");
    await expect(guestEditor).not.toContainText("next line");
    await owner.getByRole("button", { name: "Dismiss" }).click();
    await expect(owner.locator(".ai-completion-ghost")).toHaveCount(0);
  } finally {
    await ownerContext.close();
    await guestContext.close();
  }
});

test("document summary stays private and leaves shared text unchanged", async ({ browser }) => {
  const ownerContext = await browser.newContext();
  const guestContext = await browser.newContext();
  try {
    const owner = await ownerContext.newPage();
    const guest = await guestContext.newPage();
    await owner.goto("/sign-in");
    await clerk.signIn({ page: owner, emailAddress: process.env.E2E_OWNER_EMAIL! });
    await owner.goto("/documents/new");
    await owner.getByRole("button", { name: "Create document" }).click();
    const documentUrl = owner.url();
    await owner.getByRole("button", { name: "Share" }).click();
    await owner.getByRole("button", { name: "Allow signed-in editors with link" }).click();
    await owner.getByRole("button", { name: "Close sharing" }).click();

    await guest.goto("/sign-in");
    await clerk.signIn({ page: guest, emailAddress: process.env.E2E_GUEST_EMAIL! });
    await guest.goto(documentUrl);
    const ownerEditor = owner.locator('[contenteditable="true"][aria-label="Document content"]');
    const guestEditor = guest.locator('[contenteditable="true"][aria-label="Document content"]');
    await expect(ownerEditor).toBeVisible();
    await expect(guestEditor).toBeVisible();
    await ownerEditor.fill("A shared draft about spring parks.");
    await expect(guestEditor).toContainText("A shared draft about spring parks.");

    await owner.getByRole("button", { name: "Summarize", exact: true }).click();
    await expect(owner.getByRole("region", { name: "Document summary" }))
      .toContainText("Summary of: A shared draft about spring parks.");
    await expect(guest.getByRole("region", { name: "Document summary" })).toHaveCount(0);
    await expect(ownerEditor).toHaveText("A shared draft about spring parks.");
    await expect(guestEditor).toHaveText("A shared draft about spring parks.");
  } finally {
    await ownerContext.close();
    await guestContext.close();
  }
});
