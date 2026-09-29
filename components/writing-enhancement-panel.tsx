"use client";

import { X } from "lucide-react";
import type { useWritingEnhancement } from "@/components/use-writing-enhancement";

type Enhancement = ReturnType<typeof useWritingEnhancement>;

export function WritingEnhancementPanel({ enhancement, connected }: {
  enhancement: Enhancement;
  connected: boolean;
}) {
  if (!enhancement.open) return null;
  const busy = ["submitting", "queued", "running"].includes(enhancement.phase);
  const changed = enhancement.phase === "complete" && enhancement.text !== enhancement.source;
  return (
    <aside
      role="region"
      aria-labelledby="enhancement-heading"
      className="fixed top-16 right-3 z-40 flex max-h-[min(75vh,640px)] w-[min(420px,calc(100vw-24px))] flex-col overflow-auto rounded-2xl border border-[#dadce0] bg-white p-4 shadow-xl"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 id="enhancement-heading" className="text-base font-semibold">Enhance writing</h2>
        <button type="button" onClick={enhancement.close} aria-label="Close enhancement" className="rounded-full p-2 hover:bg-[#f1f3f4]">
          <X aria-hidden="true" size={18} />
        </button>
      </div>
      {enhancement.source && (
        <section className="mt-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-[#5f6368]">Original</h3>
          <p className="mt-1 whitespace-pre-wrap text-sm leading-6">{enhancement.source}</p>
        </section>
      )}
      {busy && <p role="status" className="mt-4 text-sm text-[#5f6368]">
        {enhancement.phase === "running" ? "Improving selection…" : "Preparing rewrite…"}
      </p>}
      {enhancement.phase === "complete" && (
        <section className="mt-4 border-t border-[#dadce0] pt-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-[#5f6368]">Suggested rewrite</h3>
          <p className="mt-1 whitespace-pre-wrap text-sm leading-6">{enhancement.text}</p>
          {changed ? (
            <button
              type="button"
              onClick={enhancement.apply}
              disabled={!connected || enhancement.stale}
              className="mt-4 rounded-full bg-[#1a73e8] px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              Replace selection
            </button>
          ) : <p className="mt-3 text-sm text-[#5f6368]">No changes suggested.</p>}
        </section>
      )}
      {enhancement.stale && (
        <p role="alert" className="mt-3 rounded-lg bg-[#fef7e0] p-2 text-xs text-[#735c0f]">
          Selection changed. Select text and try again.
        </p>
      )}
      {!connected && !enhancement.stale && changed && (
        <p className="mt-3 text-xs text-[#735c0f]">Reconnect to replace the selection.</p>
      )}
      {(enhancement.phase === "failed" || enhancement.phase === "error") && (
        <div className="mt-4">
          <p role="alert" className="text-sm text-[#b3261e]">{enhancement.error}</p>
          {enhancement.source && !enhancement.stale && (
            <button type="button" onClick={enhancement.retry} disabled={!connected} className="mt-3 rounded-full border border-[#dadce0] px-4 py-2 text-sm font-medium text-[#1967d2] disabled:opacity-50">
              Retry enhancement
            </button>
          )}
        </div>
      )}
    </aside>
  );
}
