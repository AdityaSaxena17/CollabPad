import { act, renderHook } from "@testing-library/react";
import { Editor } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import Collaboration from "@tiptap/extension-collaboration";
import { ySyncPluginKey } from "@tiptap/y-tiptap";
import * as Y from "yjs";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { AiCompletionPreview } from "@/components/ai-completion-preview";
import { captureCompletionContext, takeSuggestion } from "@/components/ai-completion-logic";
import { useAiCompletion } from "@/components/use-ai-completion";

vi.mock("@clerk/nextjs", () => ({ useAuth: () => ({ getToken: async () => "token" }) }));
vi.mock("@/lib/gateway", () => ({ completeText: vi.fn() }));

import { completeText } from "@/lib/gateway";

let editor: Editor;

beforeEach(() => {
  vi.useFakeTimers();
  editor = new Editor({
    element: document.createElement("div"),
    extensions: [StarterKit, AiCompletionPreview],
    content: "<p>Hello</p>",
  });
  document.body.append(editor.view.dom);
  editor.view.dom.focus();
  editor.commands.setTextSelection(editor.state.doc.content.size - 1);
  vi.mocked(completeText).mockReset();
});

afterEach(() => {
  editor.destroy();
  document.body.innerHTML = "";
  vi.useRealTimers();
});

async function finishDebounce() {
  await act(async () => {
    vi.advanceTimersByTime(750);
    await Promise.resolve();
    await Promise.resolve();
  });
}

test("splits words and explicit lines while preserving the remaining text", () => {
  expect(takeSuggestion(" next word\nline", "word")).toBe(" next ");
  expect(takeSuggestion(" next word\nline", "line")).toBe(" next word\n");
  expect(takeSuggestion(" next word", "line")).toBe(" next word");
});

test("captures bounded text around a collapsed cursor", () => {
  editor.commands.setTextSelection(3);
  expect(captureCompletionContext(editor)).toBeNull();
  editor.commands.setTextSelection(6);
  expect(captureCompletionContext(editor)).toMatchObject({ prefix: "Hello", suffix: "", pos: 6 });
  editor.commands.setContent("<p>Hello world</p>");
  editor.commands.setTextSelection(6);
  expect(captureCompletionContext(editor)).toMatchObject({ prefix: "Hello", suffix: " world", pos: 6 });
  editor.commands.selectAll();
  expect(captureCompletionContext(editor)).toBeNull();
});

test("does not request completion after editing inside a word", async () => {
  const hook = renderHook(() => useAiCompletion(editor, "doc-1", "connected"));
  act(() => {
    editor.commands.setTextSelection(3);
    editor.commands.insertContent("x");
  });
  await finishDebounce();
  expect(completeText).not.toHaveBeenCalled();
  hook.unmount();
});

test("debounces local edits and keeps preview out of document content", async () => {
  vi.mocked(completeText).mockResolvedValue(" next word\nline");
  const hook = renderHook(() => useAiCompletion(editor, "doc-1", "connected"));
  act(() => { editor.commands.insertContent("!"); });
  expect(completeText).not.toHaveBeenCalled();
  await finishDebounce();
  expect(completeText).toHaveBeenCalledTimes(1);
  expect(hook.result.current.suggestion?.text).toBe(" next word\nline");
  expect(editor.getText()).toBe("Hello!");
  expect(editor.view.dom.querySelector(".ai-completion-ghost")?.textContent).toBe(" next word\nline");
  act(() => hook.result.current.accept("word"));
  expect(editor.getText()).toBe("Hello! next ");
  expect(hook.result.current.suggestion?.text).toBe("word\nline");
  act(() => hook.result.current.accept("line"));
  expect(editor.getText()).toContain("word\n");
  act(() => hook.result.current.dismiss());
  expect(hook.result.current.suggestion).toBeNull();
  hook.unmount();
});

test("discards a stale result and requests a different suggestion on cycle", async () => {
  let resolveFirst!: (value: string) => void;
  vi.mocked(completeText).mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }));
  const hook = renderHook(() => useAiCompletion(editor, "doc-1", "connected"));
  act(() => { editor.commands.insertContent("!"); });
  await finishDebounce();
  act(() => { editor.commands.insertContent("?"); });
  await act(async () => { resolveFirst(" stale"); await Promise.resolve(); });
  expect(hook.result.current.suggestion).toBeNull();
  vi.mocked(completeText).mockResolvedValue(" fresh");
  await finishDebounce();
  expect(hook.result.current.suggestion?.text).toBe(" fresh");
  vi.mocked(completeText).mockResolvedValueOnce(" alternate");
  await act(async () => { hook.result.current.tryAnother(); await Promise.resolve(); await Promise.resolve(); });
  expect(hook.result.current.suggestion?.text).toBe(" alternate");
  hook.unmount();
});

test("remote text and formatting changes do not request completion", async () => {
  const hook = renderHook(() => useAiCompletion(editor, "doc-1", "connected"));
  act(() => {
    editor.view.dispatch(editor.state.tr.insertText(" remote", 6).setMeta(ySyncPluginKey, true));
    editor.commands.toggleBold();
  });
  await finishDebounce();
  expect(completeText).not.toHaveBeenCalled();
  hook.unmount();
});

test("moving the cursor invalidates an in-flight suggestion", async () => {
  let resolve!: (value: string) => void;
  vi.mocked(completeText).mockImplementation(() => new Promise((done) => { resolve = done; }));
  const hook = renderHook(() => useAiCompletion(editor, "doc-1", "connected"));
  act(() => { editor.commands.insertContent("!"); });
  await finishDebounce();
  act(() => {
    editor.commands.setTextSelection(2);
    editor.commands.setTextSelection(editor.state.doc.content.size - 1);
  });
  await act(async () => { resolve(" stale"); await Promise.resolve(); });
  expect(hook.result.current.suggestion).toBeNull();
  hook.unmount();
});

test("duplicate cycling preserves the current preview and Escape dismisses it", async () => {
  vi.mocked(completeText).mockResolvedValue(" same");
  const hook = renderHook(() => useAiCompletion(editor, "doc-1", "connected"));
  act(() => { editor.commands.insertContent("!"); });
  await finishDebounce();
  await act(async () => { hook.result.current.tryAnother(); await Promise.resolve(); await Promise.resolve(); });
  expect(hook.result.current.suggestion?.text).toBe(" same");
  expect(hook.result.current.error).toMatch(/different suggestion/);
  act(() => { editor.view.dom.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
  expect(hook.result.current.suggestion).toBeNull();
  hook.unmount();
});

test("a rejected completion shows no ghost or error and preserves an existing suggestion", async () => {
  vi.mocked(completeText).mockResolvedValueOnce("");
  const hook = renderHook(() => useAiCompletion(editor, "doc-1", "connected"));
  act(() => { editor.commands.insertContent("!"); });
  await finishDebounce();
  expect(hook.result.current.suggestion).toBeNull();
  expect(hook.result.current.error).toBe("");
  expect(editor.view.dom.querySelector(".ai-completion-ghost")).toBeNull();

  vi.mocked(completeText).mockResolvedValueOnce(" continuation");
  await act(async () => { hook.result.current.retry(); await Promise.resolve(); await Promise.resolve(); });
  expect(hook.result.current.suggestion?.text).toBe(" continuation");

  vi.mocked(completeText).mockResolvedValueOnce("");
  await act(async () => { hook.result.current.tryAnother(); await Promise.resolve(); await Promise.resolve(); });
  expect(hook.result.current.suggestion?.text).toBe(" continuation");
  expect(hook.result.current.error).toBe("");
  hook.unmount();
});

test("Tab accepts a visible suggestion", async () => {
  vi.mocked(completeText).mockResolvedValue(" there");
  const hook = renderHook(() => useAiCompletion(editor, "doc-1", "connected"));
  act(() => { editor.commands.insertContent("!"); });
  await finishDebounce();
  act(() => { editor.view.dom.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true })); });
  expect(editor.getText()).toBe("Hello! there");
  expect(hook.result.current.suggestion).toBeNull();
  hook.unmount();
});

test("only accepted text enters a Yjs collaborative document", async () => {
  const shared = new Y.Doc();
  const collabEditor = new Editor({
    element: document.createElement("div"),
    extensions: [StarterKit.configure({ undoRedo: false }), Collaboration.configure({ document: shared }), AiCompletionPreview],
  });
  document.body.append(collabEditor.view.dom);
  collabEditor.view.dom.focus();
  vi.mocked(completeText).mockResolvedValue(" world");
  const hook = renderHook(() => useAiCompletion(collabEditor, "doc-1", "connected"));
  act(() => { collabEditor.commands.insertContent("Hello"); });
  await finishDebounce();
  expect(hook.result.current.suggestion?.text).toBe(" world");
  expect(shared.getXmlFragment("default").toString()).not.toContain("world");
  act(() => hook.result.current.accept("all"));
  expect(shared.getXmlFragment("default").toString()).toContain("Hello world");
  hook.unmount();
  collabEditor.destroy();
  shared.destroy();
});
