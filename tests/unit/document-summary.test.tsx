import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Editor } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { DocumentSummaryPanel } from "@/components/document-summary-panel";
import { useDocumentSummary } from "@/components/use-document-summary";

vi.mock("@clerk/nextjs", () => ({ useAuth: () => ({ getToken: async () => "token" }) }));
vi.mock("@/lib/gateway", () => ({
  startSummary: vi.fn(),
  getSummaryJob: vi.fn(),
  GatewayError: class GatewayError extends Error {
    constructor(message: string, readonly status: number) { super(message); }
  },
}));

import { getSummaryJob, startSummary } from "@/lib/gateway";

let editor: Editor;
const jobId = "1bf9b73e-6d83-41b2-9b6f-f91020ace005";

beforeEach(() => {
  editor = new Editor({
    element: document.createElement("div"),
    extensions: [StarterKit],
    content: "<p>First section.</p><p>Second section.</p>",
  });
  vi.mocked(startSummary).mockReset();
  vi.mocked(getSummaryJob).mockReset();
  vi.mocked(startSummary).mockResolvedValue(jobId);
});

afterEach(() => editor.destroy());

test("summarizes the whole snapshot privately and marks later edits", async () => {
  vi.mocked(getSummaryJob).mockResolvedValue({ status: "complete", text: "A short overview.", error: "" });
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });

  function Harness() {
    const summary = useDocumentSummary(editor, "doc-1", "connected");
    return <>
      <button onClick={() => void summary.start()}>Start summary</button>
      <DocumentSummaryPanel summary={summary} />
    </>;
  }
  render(<Harness />);
  const originalText = editor.getText();
  await userEvent.click(screen.getByRole("button", { name: "Start summary" }));
  await waitFor(() => expect(startSummary).toHaveBeenCalledWith(
    "doc-1", "First section.\nSecond section.", "token",
  ));
  await waitFor(() => expect(screen.getByText("A short overview.")).toBeInTheDocument());
  expect(editor.getText()).toBe(originalText);

  act(() => editor.commands.insertContentAt(editor.state.doc.content.size - 1, " changed"));
  expect(screen.getByText(/as it was when you requested it/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Copy summary" }));
  expect(writeText).toHaveBeenCalledWith("A short overview.");
  await userEvent.click(screen.getByRole("button", { name: "Close summary" }));
  expect(screen.queryByRole("region", { name: "Document summary" })).not.toBeInTheDocument();
});

test("closing a pending job stops further polling", async () => {
  vi.mocked(getSummaryJob).mockResolvedValue({ status: "queued", text: "", error: "" });
  const hook = renderHook(() => useDocumentSummary(editor, "doc-1", "connected"));
  await act(async () => { await hook.result.current.start(); });
  await waitFor(() => expect(getSummaryJob).toHaveBeenCalledTimes(1));
  act(() => hook.result.current.close());
  await new Promise((resolve) => setTimeout(resolve, 2100));
  expect(getSummaryJob).toHaveBeenCalledTimes(1);
  hook.unmount();
});

test("a failed summary can be retried and denied access clears it", async () => {
  vi.mocked(getSummaryJob)
    .mockResolvedValueOnce({ status: "failed", text: "", error: "Could not summarize this document. Try again." })
    .mockResolvedValueOnce({ status: "complete", text: "Recovered summary.", error: "" });
  const hook = renderHook(({ status }) => useDocumentSummary(editor, "doc-1", status), {
    initialProps: { status: "connected" },
  });
  await act(async () => { await hook.result.current.start(); });
  await waitFor(() => expect(hook.result.current.phase).toBe("failed"));
  act(() => hook.result.current.retry());
  await waitFor(() => expect(hook.result.current.text).toBe("Recovered summary."));
  hook.rerender({ status: "denied" });
  await waitFor(() => expect(hook.result.current.open).toBe(false));
  expect(startSummary).toHaveBeenCalledTimes(2);
  hook.unmount();
});
