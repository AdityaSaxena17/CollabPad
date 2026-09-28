import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import { NewDocumentPrompt } from "@/app/documents/new/new-document-prompt";

const mocks = vi.hoisted(() => ({
  getToken: vi.fn(),
  createDocument: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("@clerk/nextjs", () => ({ useAuth: () => ({ getToken: mocks.getToken }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: mocks.replace }) }));
vi.mock("@/lib/gateway", () => ({ createDocument: mocks.createDocument }));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getToken.mockResolvedValue("session-token");
});

test("creates a document only after a click and navigates to it", async () => {
  mocks.createDocument.mockResolvedValue({ id: "document-1" });
  render(<NewDocumentPrompt />);
  expect(mocks.createDocument).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Create document" }));
  expect(mocks.createDocument).toHaveBeenCalledExactlyOnceWith("session-token");
  expect(mocks.replace).toHaveBeenCalledWith("/documents/document-1");
});

test("shows creation errors and allows another attempt", async () => {
  mocks.createDocument.mockRejectedValueOnce(new Error("Gateway unavailable"));
  render(<NewDocumentPrompt />);
  await userEvent.click(screen.getByRole("button", { name: "Create document" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Gateway unavailable");
  expect(screen.getByRole("button", { name: "Create document" })).toBeEnabled();
});
