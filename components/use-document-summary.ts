"use client";

import { useAuth } from "@clerk/nextjs";
import type { Editor } from "@tiptap/core";
import { useCallback, useEffect, useRef, useState } from "react";
import { GatewayError, getSummaryJob, startSummary } from "@/lib/gateway";

type SummaryPhase = "idle" | "submitting" | "queued" | "running" | "complete" | "failed" | "error";

function documentText(editor: Editor): string {
  return editor.state.doc.textBetween(0, editor.state.doc.content.size, "\n");
}

export function useDocumentSummary(editor: Editor | null, documentId: string, status: string) {
  const { getToken } = useAuth();
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<SummaryPhase>("idle");
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [stale, setStale] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState("");
  const version = useRef(0);
  const snapshot = useRef<string | null>(null);
  const jobId = useRef<string | null>(null);
  const pending = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const controller = useRef<AbortController | null>(null);

  const close = useCallback(() => {
    version.current += 1;
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    controller.current?.abort();
    controller.current = null;
    snapshot.current = null;
    jobId.current = null;
    pending.current = false;
    setOpen(false);
    setPhase("idle");
    setText("");
    setError("");
    setStale(false);
    setCopied(false);
    setCopyError("");
  }, []);

  useEffect(() => () => close(), [documentId, close]);
  useEffect(() => {
    if (status === "denied") queueMicrotask(close);
  }, [status, close]);
  useEffect(() => {
    if (!editor) return;
    const activeEditor = editor;
    function onTransaction({ transaction }: { transaction: typeof activeEditor.state.tr }) {
      if (transaction.docChanged && snapshot.current !== null &&
          documentText(activeEditor) !== snapshot.current) setStale(true);
    }
    activeEditor.on("transaction", onTransaction);
    return () => { activeEditor.off("transaction", onTransaction); };
  }, [editor]);

  async function poll(id: string, currentVersion: number) {
    try {
      const token = await getToken();
      if (!token) throw new Error("Sign in to use AI summaries.");
      if (currentVersion !== version.current) return;
      const abort = new AbortController();
      controller.current = abort;
      const result = await getSummaryJob(documentId, id, token, abort.signal);
      controller.current = null;
      if (currentVersion !== version.current) return;
      if (result.status === "complete") {
        setPhase("complete");
        setText(result.text);
        pending.current = false;
        jobId.current = null;
      } else if (result.status === "failed") {
        setPhase("failed");
        setError(result.error || "Could not summarize this document. Try again.");
        pending.current = false;
        jobId.current = null;
      } else {
        setPhase(result.status);
        timer.current = setTimeout(() => void poll(id, currentVersion), 2_000);
      }
    } catch (cause) {
      if (currentVersion !== version.current) return;
      setError(cause instanceof Error ? cause.message : "Summary service is unavailable.");
      setPhase("error");
      pending.current = false;
      if (cause instanceof GatewayError && cause.status === 404) jobId.current = null;
    }
  }

  async function start() {
    if (!editor || status !== "connected" || pending.current) return;
    const source = documentText(editor);
    close();
    setOpen(true);
    if (!source.trim() || source.length > 100_000) {
      setError(source.length > 100_000
        ? "This document is too long to summarize."
        : "Add text before summarizing.");
      setPhase("error");
      return;
    }
    snapshot.current = source;
    const currentVersion = version.current;
    pending.current = true;
    setPhase("submitting");
    try {
      const token = await getToken();
      if (!token) throw new Error("Sign in to use AI summaries.");
      if (currentVersion !== version.current) return;
      const id = await startSummary(documentId, source, token);
      if (currentVersion !== version.current) return;
      jobId.current = id;
      setPhase("queued");
      void poll(id, currentVersion);
    } catch (cause) {
      if (currentVersion !== version.current) return;
      pending.current = false;
      setPhase("error");
      setError(cause instanceof Error ? cause.message : "Summary service is unavailable.");
    }
  }

  function retry() {
    if (pending.current) return;
    if (jobId.current) {
      setError("");
      setPhase("queued");
      pending.current = true;
      void poll(jobId.current, version.current);
    } else {
      void start();
    }
  }

  async function copy() {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setCopyError("");
    } catch {
      setCopyError("Could not copy the summary. Select the text and copy it instead.");
    }
  }

  return { open, phase, text, error, stale, copied, copyError, start, retry, copy, close };
}
