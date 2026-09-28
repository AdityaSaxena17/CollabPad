import { clerk } from "@clerk/testing/playwright";
import { expect, test } from "@playwright/test";

test("two signed-in editors share, save, and revoke a document", async ({ browser }) => {
  const ownerContext = await browser.newContext();
  const guestContext = await browser.newContext();
  try {
    const owner = await ownerContext.newPage();
    const guest = await guestContext.newPage();
    await owner.goto("/sign-in");
    await clerk.signIn({ page: owner, emailAddress: process.env.E2E_OWNER_EMAIL! });
    await owner.goto("/documents/new");
    await owner.getByRole("button", { name: "Create document" }).click();
    await expect(owner).toHaveURL(/\/documents\/[0-9a-f-]{36}$/i);
    const documentUrl = owner.url();

    await owner.getByRole("button", { name: "Share" }).click();
    await owner.getByRole("button", { name: "Allow signed-in editors with link" }).click();
    await expect(owner.getByText("Anyone signed in with this link can edit.")).toBeVisible();
    await owner.getByRole("button", { name: "Close sharing" }).click();

    await guest.goto("/sign-in");
    await clerk.signIn({ page: guest, emailAddress: process.env.E2E_GUEST_EMAIL! });
    await guest.goto(documentUrl);
    const ownerEditor = owner.locator('[contenteditable="true"][aria-label="Document content"]');
    const guestEditor = guest.locator('[contenteditable="true"][aria-label="Document content"]');
    await expect(ownerEditor).toBeVisible();
    await expect(guestEditor).toBeVisible();

    await ownerEditor.fill("Owner wrote this.");
    await expect(guestEditor).toContainText("Owner wrote this.");
    await expect(owner.locator(".document-footer [role=status]")).toContainText("Saved");

    await guestEditor.press("End");
    await guestEditor.type(" Guest added more.");
    await expect(ownerEditor).toContainText("Guest added more.");
    await expect(guest.locator(".document-footer [role=status]")).toContainText("Saved");
    await expect(owner.getByLabel(/other editors online/)).toBeVisible();

    await owner.getByRole("button", { name: "Share" }).click();
    await owner.getByRole("button", { name: "Turn off link access" }).click();
    await expect(guest.getByRole("heading", { name: "Document unavailable" })).toBeVisible();
  } finally {
    await ownerContext.close();
    await guestContext.close();
  }
});
