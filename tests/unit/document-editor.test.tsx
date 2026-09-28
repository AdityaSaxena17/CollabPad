import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Awareness } from "y-protocols/awareness";
import * as Y from "yjs";
import { expect, test, vi } from "vitest";
import { DocumentEditor } from "@/components/document-editor";

vi.mock("@clerk/nextjs", () => ({
  UserButton: () => null,
  useAuth: () => ({ getToken: async () => "token" }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

function renderEditor(isOwner = true) {
  const sharedDocument = new Y.Doc();
  const awareness = new Awareness(sharedDocument);
  const onSetSharing = vi.fn().mockResolvedValue(undefined);
  const onRename = vi.fn();
  render(
    <DocumentEditor
      document={{
        id: "document-1",
        title: "Draft",
        ownerId: "owner",
        shareEnabled: false,
        updatedAt: "2026-09-27T00:00:00Z",
      }}
      sharedDocument={sharedDocument}
      awareness={awareness}
      status="connected"
      presenceStatus="available"
      connections={[]}
      currentUserId={isOwner ? "owner" : "guest"}
      pendingCount={0}
      isOwner={isOwner}
      onRename={onRename}
      onSetSharing={onSetSharing}
    />,
  );
  return { onSetSharing, onRename, awareness, sharedDocument };
}

test("lets the owner enable sharing through an explicit confirmation", async () => {
  const { onSetSharing } = renderEditor();
  await userEvent.click(screen.getByRole("button", { name: "Share" }));
  expect(screen.getByRole("dialog", { name: "Share this document" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Allow signed-in editors with link" }));
  expect(onSetSharing).toHaveBeenCalledWith(true);
});

test("does not let a guest manage sharing", () => {
  renderEditor(false);
  expect(screen.getByRole("button", { name: "Share" })).toBeDisabled();
});
