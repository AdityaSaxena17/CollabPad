import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Editor } from "@tiptap/core";
import Collaboration from "@tiptap/extension-collaboration";
import StarterKit from "@tiptap/starter-kit";
import { ySyncPluginKey } from "@tiptap/y-tiptap";
import * as Y from "yjs";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { captureEnhancementSelection } from "@/components/ai-enhancement-logic";
import { useWritingEnhancement } from "@/components/use-writing-enhancement";
import { WritingEnhancementPanel } from "@/components/writing-enhancement-panel";

vi.mock("@clerk/nextjs", () => ({ useAuth: () => ({ getToken: async () => "token" }) }));
vi.mock("@/lib/gateway", () => ({
  startEnhancement: vi.fn(),
  getEnhancementJob: vi.fn(),
  GatewayError: class GatewayError extends Error {
    constructor(message: string, readonly status: number) { super(message); }
  },
}));

import { getEnhancementJob, startEnhancement } from "@/lib/gateway";

let editor: Editor;
const jobId = "1bf9b73e-6d83-41b2-9b6f-f91020ace005";

beforeEach(() => {
  editor = new Editor({
    element: document.createElement("div"),
    extensions: [StarterKit],
    content: "<p>Before <strong>draft</strong> after.</p>",
  });
  vi.mocked(startEnhancement).mockReset().mockResolvedValue(jobId);
  vi.mocked(getEnhancementJob).mockReset();
});

afterEach(() => editor.destroy());

function selectDraft(target = editor) {
  target.commands.setTextSelection({ from: 8, to: 13 });
}

test("accepts one consistently styled text range and rejects mixed or cross-block selections", () => {
  selectDraft();
  expect(captureEnhancementSelection(editor)).toMatchObject({ source: "draft", from: 8, to: 13 });
  editor.commands.setTextSelection({ from: 1, to: 13 });
  expect(captureEnhancementSelection(editor)).toMatch(/consistent format/);
  editor.commands.setContent("<p>First</p><p>Second</p>");
  editor.commands.setTextSelection({ from: 1, to: 12 });
  expect(captureEnhancementSelection(editor)).toMatch(/one paragraph/);
});

test("keeps the rewrite private, maps edits before it, and preserves bold on replacement", async () => {
  vi.mocked(getEnhancementJob).mockResolvedValue({ status: "complete", text: "clear draft", error: "" });
  selectDraft();
  const transactions = vi.fn();
  editor.on("transaction", transactions);
  function Harness() {
    const enhancement = useWritingEnhancement(editor, "doc-1", "connected");
    return <>
      <button onClick={enhancement.start}>Start</button>
      <WritingEnhancementPanel enhancement={enhancement} connected />
    </>;
  }
  render(<Harness />);
  await userEvent.click(screen.getByRole("button", { name: "Start" }));
  await waitFor(() => expect(screen.getByText("clear draft")).toBeInTheDocument());
  expect(editor.getText()).toBe("Before draft after.");
  act(() => editor.view.dispatch(editor.state.tr.insertText("New ", 1).setMeta(ySyncPluginKey, true)));
  await userEvent.click(screen.getByRole("button", { name: "Replace selection" }));
  expect(editor.getText()).toBe("New Before clear draft after.");
  expect(editor.getHTML()).toContain("<strong>clear draft</strong>");
  expect(transactions.mock.calls.filter(([event]) => event.transaction.getMeta("ai-enhancement-accepted"))).toHaveLength(1);
});

test("an edit inside the captured range makes the preview stale and blocks replacement", async () => {
  vi.mocked(getEnhancementJob).mockResolvedValue({ status: "complete", text: "clear draft", error: "" });
  selectDraft();
  const hook = renderHook(() => useWritingEnhancement(editor, "doc-1", "connected"));
  act(() => hook.result.current.start());
  await waitFor(() => expect(hook.result.current.phase).toBe("complete"));
  act(() => editor.view.dispatch(editor.state.tr.insertText("!", 10).setMeta(ySyncPluginKey, true)));
  expect(hook.result.current.stale).toBe(true);
  act(() => hook.result.current.apply());
  expect(editor.getText()).toBe("Before dr!aft after.");
  hook.unmount();
});

test("denied access clears a private result and disconnect prevents acceptance", async () => {
  vi.mocked(getEnhancementJob).mockResolvedValue({ status: "complete", text: "clear draft", error: "" });
  selectDraft();
  const hook = renderHook(({ status }) => useWritingEnhancement(editor, "doc-1", status), {
    initialProps: { status: "connected" },
  });
  act(() => hook.result.current.start());
  await waitFor(() => expect(hook.result.current.phase).toBe("complete"));
  hook.rerender({ status: "disconnected" });
  act(() => hook.result.current.apply());
  expect(editor.getText()).toContain("draft");
  hook.rerender({ status: "denied" });
  await waitFor(() => expect(hook.result.current.open).toBe(false));
  hook.unmount();
});

test("only applying the preview changes the collaborative document", async () => {
  const shared = new Y.Doc();
  const collabEditor = new Editor({
    element: document.createElement("div"),
    extensions: [StarterKit.configure({ undoRedo: false }), Collaboration.configure({ document: shared })],
  });
  vi.mocked(getEnhancementJob).mockResolvedValue({ status: "complete", text: "Better sentence.", error: "" });
  act(() => collabEditor.commands.insertContent("Draft sentence."));
  collabEditor.commands.setTextSelection({ from: 1, to: 16 });
  const hook = renderHook(() => useWritingEnhancement(collabEditor, "doc-1", "connected"));
  act(() => hook.result.current.start());
  await waitFor(() => expect(hook.result.current.phase).toBe("complete"));
  expect(shared.getXmlFragment("default").toString()).toContain("Draft sentence.");
  expect(shared.getXmlFragment("default").toString()).not.toContain("Better sentence.");
  act(() => hook.result.current.apply());
  expect(shared.getXmlFragment("default").toString()).toContain("Better sentence.");
  hook.unmount();
  collabEditor.destroy();
  shared.destroy();
});
