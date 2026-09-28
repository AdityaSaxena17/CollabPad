import { beforeEach, expect, test, vi } from "vitest";
import {
  createDocument,
  getDocument,
  listDocuments,
  setDocumentSharing,
  completeText,
} from "@/lib/gateway";

const document = {
  id: "d77d39fb-9157-445c-aacd-729ee3230ac0",
  title: "Draft",
  ownerId: "user_1",
  shareEnabled: false,
  updatedAt: "2026-09-27T00:00:00Z",
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

test("sends the bearer token and rejects malformed document data", async () => {
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ documents: [document] }) } as Response);

  await expect(listDocuments("session-token")).resolves.toEqual([document]);
  expect(fetchMock).toHaveBeenCalledWith(
    "http://localhost:18080/api/documents",
    expect.objectContaining({
      cache: "no-store",
      headers: { Authorization: "Bearer session-token" },
    }),
  );

  fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ documents: [{ id: 3 }] }) } as Response);
  await expect(listDocuments("session-token")).rejects.toThrow("invalid document list");
});

test("creates and shares documents through the authenticated gateway", async () => {
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockResolvedValueOnce({ ok: true, json: async () => document } as Response);
  fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ ...document, shareEnabled: true }) } as Response);

  await expect(createDocument("token")).resolves.toEqual(document);
  await expect(setDocumentSharing(document.id, true, "token")).resolves.toMatchObject({ shareEnabled: true });
  expect(fetchMock).toHaveBeenNthCalledWith(
    1,
    "http://localhost:18080/api/documents",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ title: "Untitled document" }),
    }),
  );
  expect(fetchMock).toHaveBeenNthCalledWith(
    2,
    `http://localhost:18080/api/documents/${document.id}/sharing`,
    expect.objectContaining({ method: "PATCH", body: JSON.stringify({ enabled: true }) }),
  );
});

test("reports access denial and network failure clearly", async () => {
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockResolvedValueOnce({ ok: false, status: 404 } as Response);
  await expect(getDocument(document.id, "token")).rejects.toMatchObject({ status: 404 });

  fetchMock.mockRejectedValueOnce(new Error("offline"));
  await expect(getDocument(document.id, "token")).rejects.toMatchObject({ status: 503 });
});

test("requests and validates plain-text completion", async () => {
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ text: " next line" }) } as Response);
  await expect(completeText(document.id, "before", "after", "token")).resolves.toBe(" next line");
  expect(fetchMock).toHaveBeenCalledWith(
    `http://localhost:18080/api/documents/${document.id}/ai/completion`,
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ prefix: "before", suffix: "after" }),
      headers: { Authorization: "Bearer token", "Content-Type": "application/json" },
    }),
  );
  fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ text: "\u0000" }) } as Response);
  await expect(completeText(document.id, "before", "", "token")).rejects.toMatchObject({ status: 502 });
  fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ text: "" }) } as Response);
  await expect(completeText(document.id, "before", "", "token")).resolves.toBe("");
  fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ text: "   " }) } as Response);
  await expect(completeText(document.id, "before", "", "token")).rejects.toMatchObject({ status: 502 });
});
