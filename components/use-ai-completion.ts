"use client";

import { useAuth } from "@clerk/nextjs";
import type { Editor } from "@tiptap/core";
import { TextSelection } from "@tiptap/pm/state";
import { ySyncPluginKey } from "@tiptap/y-tiptap";
import { useCallback, useEffect, useRef, useState } from "react";
import { completeText } from "@/lib/gateway";
import { setCompletionPreview } from "@/components/ai-completion-preview";
import {
  captureCompletionContext,
  suggestionIsCurrent,
  takeSuggestion,
  type Suggestion,
} from "@/components/ai-completion-logic";

const DEBOUNCE_MS = 750;
const AI_ACCEPTED_META = "ai-completion-accepted";

export function useAiCompletion(editor: Editor | null, documentId: string, status: string) {
  const { getToken } = useAuth();
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const suggestionRef = useRef<Suggestion | null>(null);
  const anchorPos = useRef<number | null>(null);
  const statusRef = useRef(status);
  const version = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlight = useRef(false);
  const queued = useRef(false);
  const requestRef = useRef<(previous?: string, manual?: boolean) => Promise<void>>(async () => {});
  const acceptRef = useRef<(mode: "all" | "word" | "line") => void>(() => {});
  const dismissRef = useRef<() => void>(() => {});

  useEffect(() => { statusRef.current = status; }, [status]);

  const show = useCallback((next: Suggestion | null) => {
    suggestionRef.current = next;
    setSuggestion(next);
  }, []);

  const cancelTimer = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  const invalidate = useCallback(() => {
    version.current += 1;
    cancelTimer();
    queued.current = false;
    anchorPos.current = null;
    show(null);
    setError("");
  }, [cancelTimer, show]);

  const schedule = useCallback(() => {
    cancelTimer();
    timer.current = setTimeout(() => {
      timer.current = null;
      void requestRef.current();
    }, DEBOUNCE_MS);
  }, [cancelTimer]);

  async function requestCompletion(previous?: string, manual = false) {
    if (!editor || statusRef.current !== "connected" || editor.view.composing) return;
    if (inFlight.current) {
      queued.current = true;
      return;
    }
    const captured = captureCompletionContext(editor);
    if (!captured || (!manual && !editor.isFocused)) return;
    anchorPos.current = captured.pos;
    const requestVersion = version.current;
    inFlight.current = true;
    setLoading(true);
    setError("");
    try {
      const token = await getToken();
      if (!token) throw new Error("Sign in to use AI completion.");
      if (requestVersion !== version.current) return;
      const text = await completeText(documentId, captured.prefix, captured.suffix, token);
      if (requestVersion !== version.current || statusRef.current !== "connected" ||
          (!manual && !editor.isFocused) || !suggestionIsCurrent(editor, { ...captured, text })) return;
      if (!text) return;
      if (text === previous) {
        setError("No different suggestion was available. Try again.");
        return;
      }
      show({ text, pos: captured.pos, doc: captured.doc });
    } catch (cause) {
      if (requestVersion === version.current) {
        setError(cause instanceof Error ? cause.message : "AI completion is unavailable.");
      }
    } finally {
      inFlight.current = false;
      setLoading(false);
      if (queued.current && statusRef.current === "connected") {
        queued.current = false;
        schedule();
      }
    }
  }
  useEffect(() => { requestRef.current = requestCompletion; });

  useEffect(() => {
    if (!editor) return;
    setCompletionPreview(editor, suggestion ? { pos: suggestion.pos, text: suggestion.text } : null);
  }, [editor, suggestion]);

  useEffect(() => {
    if (status !== "connected") {
      queueMicrotask(() => {
        if (statusRef.current !== "connected") invalidate();
      });
    }
  }, [status, invalidate]);

  useEffect(() => {
    if (!editor) return;
    const activeEditor = editor;
    const editorDom = editor.view.dom;
    function onTransaction({ transaction }: { transaction: typeof activeEditor.state.tr }) {
      if (transaction.docChanged) {
        if (transaction.getMeta(AI_ACCEPTED_META)) return;
        const local = !transaction.getMeta(ySyncPluginKey);
        const oldText = transaction.before.textBetween(0, transaction.before.content.size, "\n");
        const newText = transaction.doc.textBetween(0, transaction.doc.content.size, "\n");
        invalidate();
        if (local && oldText !== newText && statusRef.current === "connected" &&
            activeEditor.isFocused && transaction.selection.empty) schedule();
      } else if (transaction.selectionSet && anchorPos.current !== null &&
                 transaction.selection.from !== anchorPos.current) {
        invalidate();
      }
    }
    activeEditor.on("transaction", onTransaction);
    function onKeyDown(event: KeyboardEvent) {
      if (!suggestionRef.current || statusRef.current !== "connected") return;
      if (event.key === "Tab" && !event.shiftKey) {
        event.preventDefault();
        event.stopImmediatePropagation();
        acceptRef.current("all");
      } else if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        dismissRef.current();
      }
    }
    editorDom.addEventListener("keydown", onKeyDown, true);
    return () => {
      activeEditor.off("transaction", onTransaction);
      editorDom.removeEventListener("keydown", onKeyDown, true);
      version.current += 1;
      cancelTimer();
      anchorPos.current = null;
      suggestionRef.current = null;
      if (!activeEditor.isDestroyed) setCompletionPreview(activeEditor, null);
    };
  }, [editor, invalidate, schedule, cancelTimer]);

  function accept(mode: "all" | "word" | "line") {
    const current = suggestionRef.current;
    if (!editor || statusRef.current !== "connected" || !current) return;
    if (!suggestionIsCurrent(editor, current)) {
      invalidate();
      return;
    }
    const accepted = takeSuggestion(current.text, mode);
    const remainder = current.text.slice(accepted.length);
    const marks = editor.state.storedMarks ?? editor.state.selection.$from.marks();
    const parts = accepted.split("\n");
    const nodes = parts.flatMap((part, index) => [
      ...(index > 0 ? [editor.state.schema.nodes.hardBreak.create()] : []),
      ...(part ? [editor.state.schema.text(part, marks)] : []),
    ]);
    if (!nodes.length) return;
    const newPos = current.pos + nodes.reduce((size, node) => size + node.nodeSize, 0);
    const transaction = editor.state.tr.replaceWith(current.pos, current.pos, nodes);
    transaction.setSelection(TextSelection.create(transaction.doc, newPos));
    transaction.setMeta(AI_ACCEPTED_META, true);
    version.current += 1;
    cancelTimer();
    queued.current = false;
    anchorPos.current = remainder ? newPos : null;
    editor.view.dispatch(transaction);
    editor.commands.focus();
    show(remainder ? { text: remainder, pos: newPos, doc: editor.state.doc } : null);
    setError("");
  }

  function dismiss() {
    invalidate();
  }
  useEffect(() => {
    acceptRef.current = accept;
    dismissRef.current = dismiss;
  });

  function tryAnother() {
    if (suggestionRef.current && !loading) void requestRef.current(suggestionRef.current.text, true);
  }

  function retry() {
    if (!loading) void requestRef.current(undefined, true);
  }

  return { suggestion, loading, error, accept, dismiss, tryAnother, retry };
}
