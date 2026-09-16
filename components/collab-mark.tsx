import { FileText } from "lucide-react";

type CollabMarkProps = {
  compact?: boolean;
};

/** Renders the shared Collab Docs product mark without external image assets. */
export function CollabMark({ compact = false }: CollabMarkProps) {
  return (
    <span className="inline-flex items-center gap-3">
      <span className="relative grid size-10 shrink-0 place-items-center overflow-hidden rounded-lg bg-[#1a73e8] text-white shadow-sm">
        <FileText aria-hidden="true" size={24} strokeWidth={2.1} />
        <span className="absolute right-1.5 bottom-2 h-px w-4 bg-white/70" />
        <span className="absolute right-1.5 bottom-3.5 h-px w-4 bg-white/70" />
      </span>
      {!compact && (
        <span className="text-[21px] font-medium tracking-[-0.02em] text-[#3c4043]">
          Collab Docs
        </span>
      )}
    </span>
  );
}
