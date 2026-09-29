"use client";

import { useAuth } from "@clerk/nextjs";
import type { Editor } from "@tiptap/core";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  captureEnhancementSelection,
  enhancementSelectionIsCurrent,
  mapEnhancementSelection,
  type EnhancementSelection,
} from "@/components/ai-enhancement-logic";
import { GatewayError, getEnhancementJob, startEnhancement } from "@/lib/gateway";

type EnhancementPhase = "idle" | "submitting" | "queued" | "running" | "complete" | "failed" | "error";

export function useWritingEnhancement(editor: Editor | null, documentId: string, status: string) {
  const { getToken } = useAuth();
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<EnhancementPhase>("idle");
  const [source, setSource] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [stale, setStale] = useState(false);
  const selected = useRef<EnhancementSelection | null>(null);
  const staleRef = useRef(false);
  const jobId = useRef<string | null>(null);
  const pending = useRef(false);
  const version = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const controller = useRef<AbortController | null>(null);

  const close = useCallback(() => {
    version.current += 1;
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    controller.current?.abort();
    controller.current = null;
    selected.current = null;
    staleRef.current = false;
    jobId.current = null;
    pending.current = false;
    setOpen(false);
    setPhase("idle");
    setSource("");
    setText("");
    setError("");
    setStale(false);
  }, []);

  useEffect(() => () => close(), [documentId, close]);
  useEffect(() => {
    if (status === "denied") queueMicrotask(close);
  }, [status, close]);
  useEffect(() => {
    if (!editor) return;
    const activeEditor = editor;
    function onTransaction({ transaction }: { transaction: typeof activeEditor.state.tr }) {
      if (!selected.current || staleRef.current || !transaction.docChanged) return;
      const mapped = mapEnhancementSelection(selected.current, transaction);
      if (mapped) selected.current = mapped;
      else {
        staleRef.current = true;
        setStale(true);
      }
    }
    activeEditor.on("transaction", onTransaction);
    return () => { activeEditor.off("transaction", onTransaction); };
  }, [editor]);

  async function poll(id: string, currentVersion: number) {
    try {
      const token = await getToken();
      if (!token) throw new Error("Sign in to use AI enhancement.");
      if (currentVersion !== version.current) return;
      const abort = new AbortController();
      controller.current = abort;
      const result = await getEnhancementJob(documentId, id, token, abort.signal);
      controller.current = null;
      if (currentVersion !== version.current) return;
      if (result.status === "complete") {
        setPhase("complete");
        setText(result.text);
        pending.current = false;
        jobId.current = null;
      } else if (result.status === "failed") {
        setPhase("failed");
        setError(result.error || "Could not improve this selection. Try again.");
        pending.current = false;
        jobId.current = null;
      } else {
        setPhase(result.status);
        timer.current = setTimeout(() => void poll(id, currentVersion), 2_000);
      }
    } catch (cause) {
      if (currentVersion !== version.current) return;
      setPhase("error");
      setError(cause instanceof Error ? cause.message : "Enhancement service is unavailable.");
      pending.current = false;
      if (cause instanceof GatewayError && cause.status === 404) jobId.current = null;
    }
  }

  async function submit(selection: EnhancementSelection) {
    const currentVersion = version.current;
    pending.current = true;
    setPhase("submitting");
    setText("");
    setError("");
    try {
      const token = await getToken();
      if (!token) throw new Error("Sign in to use AI enhancement.");
      if (currentVersion !== version.current) return;
      const id = await startEnhancement(documentId, selection.source, token);
      if (currentVersion !== version.current) return;
      jobId.current = id;
      setPhase("queued");
      void poll(id, currentVersion);
    } catch (cause) {
      if (currentVersion !== version.current) return;
      pending.current = false;
      setPhase("error");
      setError(cause instanceof Error ? cause.message : "Enhancement service is unavailable.");
    }
  }

  function start() {
    if (!editor || status !== "connected" || pending.current) return;
    const captured = captureEnhancementSelection(editor);
    close();
    setOpen(true);
    if (typeof captured === "string") {
      setPhase("error");
      setError(captured);
      return;
    }
    selected.current = captured;
    setSource(captured.source);
    void submit(captured);
  }

  function retry() {
    if (!editor || status !== "connected" || pending.current) return;
    const current = selected.current;
    if (!current || staleRef.current || !enhancementSelectionIsCurrent(editor, current)) {
      staleRef.current = true;
      setStale(true);
      setError("Selection changed. Select text and try again.");
      return;
    }
    if (jobId.current) {
      setPhase("queued");
      setError("");
      pending.current = true;
      void poll(jobId.current, version.current);
    } else {
      void submit(current);
    }
  }

  function apply() {
    const current = selected.current;
    if (!editor || status !== "connected" || !current || phase !== "complete" ||
        !text || text === current.source || staleRef.current) return;
    if (!enhancementSelectionIsCurrent(editor, current)) {
      staleRef.current = true;
      setStale(true);
      return;
    }
    const transaction = editor.state.tr.replaceWith(
      current.from, current.to, editor.state.schema.text(text, [...current.marks]),
    );
    transaction.setMeta("ai-enhancement-accepted", true);
    editor.view.dispatch(transaction);
    close();
    editor.commands.focus();
  }

  return { open, phase, source, text, error, stale, start, retry, apply, close };
}
