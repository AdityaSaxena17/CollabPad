import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import { DocumentsDashboard } from "@/app/documents/documents-dashboard";

const mocks = vi.hoisted(() => ({
  getToken: vi.fn(),
  listDocuments: vi.fn(),
}));

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: mocks.getToken, isLoaded: true }),
  UserButton: () => null,
}));
vi.mock("@/lib/gateway", () => ({ listDocuments: mocks.listDocuments }));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getToken.mockResolvedValue("session-token");
});

test("loads the owner's documents and filters by title", async () => {
  mocks.listDocuments.mockResolvedValue([
    { id: "one", title: "Alpha notes", ownerId: "owner", shareEnabled: false, updatedAt: "2026-09-27T00:00:00Z" },
    { id: "two", title: "Beta draft", ownerId: "owner", shareEnabled: false, updatedAt: "2026-09-27T00:00:00Z" },
  ]);
  render(<DocumentsDashboard />);

  expect(await screen.findByText("Alpha notes")).toBeInTheDocument();
  expect(mocks.listDocuments).toHaveBeenCalledWith("session-token");
  await userEvent.type(screen.getByRole("searchbox", { name: "Search documents" }), "beta");
  expect(screen.queryByText("Alpha notes")).not.toBeInTheDocument();
  expect(screen.getByText("Beta draft")).toBeInTheDocument();
});

test("shows a useful error when the gateway cannot load documents", async () => {
  mocks.listDocuments.mockRejectedValue(new Error("Gateway unavailable"));
  render(<DocumentsDashboard />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Gateway unavailable");
});
