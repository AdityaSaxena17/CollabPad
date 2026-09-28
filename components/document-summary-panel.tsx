"use client";

import { X } from "lucide-react";
import type { useDocumentSummary } from "@/components/use-document-summary";

type Summary = ReturnType<typeof useDocumentSummary>;

export function DocumentSummaryPanel({ summary }: { summary: Summary }) {
  if (!summary.open) return null;
  const busy = ["submitting", "queued", "running"].includes(summary.phase);
  return (
    <aside
      role="region"
      aria-labelledby="summary-heading"
      className="fixed top-16 right-3 z-40 flex max-h-[min(75vh,640px)] w-[min(400px,calc(100vw-24px))] flex-col rounded-2xl border border-[#dadce0] bg-white p-4 shadow-xl"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 id="summary-heading" className="text-base font-semibold">Document summary</h2>
        <button type="button" onClick={summary.close} aria-label="Close summary" className="rounded-full p-2 hover:bg-[#f1f3f4]">
          <X aria-hidden="true" size={18} />
        </button>
      </div>
      {busy && (
        <p role="status" className="mt-3 text-sm text-[#5f6368]">
          {summary.phase === "running" ? "Summarizing document…" : "Preparing summary…"}
        </p>
      )}
      {summary.stale && (
        <p className="mt-3 rounded-lg bg-[#fef7e0] p-2 text-xs text-[#735c0f]">
          This summarizes the document as it was when you requested it.
        </p>
      )}
      {summary.phase === "complete" && (
        <div className="mt-3 min-h-0 overflow-auto">
          <p className="whitespace-pre-wrap text-sm leading-6 text-[#202124]">{summary.text}</p>
          <button type="button" onClick={() => void summary.copy()} className="mt-4 rounded-full bg-[#1a73e8] px-4 py-2 text-sm font-medium text-white">
            {summary.copied ? "Copied" : "Copy summary"}
          </button>
          {summary.copyError && <p role="alert" className="mt-2 text-xs text-[#b3261e]">{summary.copyError}</p>}
        </div>
      )}
      {(summary.phase === "error" || summary.phase === "failed") && (
        <div className="mt-3">
          <p role="alert" className="text-sm text-[#b3261e]">{summary.error}</p>
          <button type="button" onClick={summary.retry} className="mt-3 rounded-full border border-[#dadce0] px-4 py-2 text-sm font-medium text-[#1967d2]">Retry summary</button>
        </div>
      )}
    </aside>
  );
}
