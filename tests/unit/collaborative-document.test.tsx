import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import * as Y from "yjs";
import { CollaborativeDocument } from "@/components/collaborative-document";

const mocks = vi.hoisted(() => ({
  getToken: vi.fn(),
  getDocument: vi.fn(),
}));

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: mocks.getToken, isLoaded: true, userId: "owner" }),
}));
vi.mock("@/lib/gateway", () => ({
  gatewayWebSocketUrl: "ws://gateway",
  getDocument: mocks.getDocument,
  setDocumentSharing: vi.fn(),
}));
vi.mock("@/components/document-editor", () => ({
  DocumentEditor: ({ sharedDocument, status, pendingCount }: {
    sharedDocument: Y.Doc;
    status: string;
    pendingCount: number;
  }) => (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="pending">{pendingCount}</span>
      <button onClick={() => sharedDocument.getText("test").insert(0, "A")}>Make edit</button>
    </div>
  ),
}));

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  sent: Record<string, unknown>[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  send(payload: string) {
    this.sent.push(JSON.parse(payload));
  }

  receive(message: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent);
  }

  close() {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.onclose?.();
  }
}

const documentId = "d77d39fb-9157-445c-aacd-729ee3230ac0";
const snapshot = Buffer.from(Y.encodeStateAsUpdate(new Y.Doc())).toString("base64");

beforeEach(() => {
  vi.clearAllMocks();
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  mocks.getToken.mockResolvedValue("session-token");
  mocks.getDocument.mockResolvedValue({
    id: documentId,
    title: "Draft",
    ownerId: "owner",
    shareEnabled: false,
    updatedAt: "2026-09-27T00:00:00Z",
  });
});

async function openDocument() {
  render(<CollaborativeDocument documentId={documentId} />);
  await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
  const socket = FakeWebSocket.instances[0];
  act(() => {
    socket.open();
    socket.receive({ type: "snapshot", update: snapshot, title: "Draft" });
  });
  expect(await screen.findByTestId("status")).toHaveTextContent("connected");
  return socket;
}

test("resends an unacknowledged edit after reconnect and clears it on acknowledgement", async () => {
  const firstSocket = await openDocument();
  fireEvent.click(screen.getByRole("button", { name: "Make edit" }));
  expect(screen.getByTestId("pending")).toHaveTextContent("1");
  await waitFor(() => expect(firstSocket.sent.some((message) => message.type === "edit")).toBe(true));
  const edit = firstSocket.sent.find((message) => message.type === "edit")!;

  act(() => firstSocket.close());
  expect(screen.getByTestId("status")).toHaveTextContent("disconnected");
  await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2), { timeout: 3_000 });
  const secondSocket = FakeWebSocket.instances[1];
  act(() => {
    secondSocket.open();
    secondSocket.receive({ type: "snapshot", update: snapshot, title: "Draft" });
  });
  expect(secondSocket.sent).toContainEqual(edit);
  act(() => secondSocket.receive({ type: "ack", id: edit.id }));
  expect(screen.getByTestId("pending")).toHaveTextContent("0");
});

test("stops editing when document access is revoked", async () => {
  const socket = await openDocument();
  act(() => socket.receive({ type: "access_revoked" }));
  expect(await screen.findByRole("heading", { name: "Document unavailable" })).toBeInTheDocument();
});
