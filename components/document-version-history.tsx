"use client";

import { useAuth } from "@clerk/nextjs";
import Collaboration from "@tiptap/extension-collaboration";
import Highlight from "@tiptap/extension-highlight";
import TextAlign from "@tiptap/extension-text-align";
import { TextStyleKit } from "@tiptap/extension-text-style";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { History } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import * as Y from "yjs";
import {
  DocumentPagination,
  PAGE_BOTTOM,
  PAGE_GAP,
  PAGE_HEIGHT,
  useDocumentPagination,
} from "@/components/use-document-pagination";
import { getDocumentVersion, listDocumentVersions, type VersionMetadata } from "@/lib/gateway";

type Version = VersionMetadata & { update: string };

/** Matches the editor's page width in globals.css. */
const PAGE_WIDTH = 816;

function VersionPreview({ update }: { update: string }) {
  const [document] = useState(() => {
    const snapshot = new Y.Doc();
    Y.applyUpdate(snapshot, Uint8Array.from(atob(update), (byte) => byte.charCodeAt(0)));
    return snapshot;
  });
  const editor = useEditor({
    immediatelyRender: false,
    editable: false,
    extensions: [
      StarterKit.configure({ undoRedo: false, link: { openOnClick: false } }),
      TextAlign.configure({ types: ["heading", "paragraph"] }),
      TextStyleKit,
      Highlight.configure({ multicolor: true }),
      Collaboration.configure({ document }),
      DocumentPagination,
    ],
  }, [document]);

  // Render a real page, then zoom it to the panel width. `zoom` (unlike a
  // transform) also scales layout height, so the panel scrolls correctly.
  const frame = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      setScale(Math.min(1, entry.contentRect.width / PAGE_WIDTH));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const pageCount = useDocumentPagination(editor, scale * 100);

  // Same page stack, spacers and masks as the main editor, so text breaks
  // onto the same pages it does there.
  return (
    <div ref={frame} className="mt-3 rounded-lg bg-[#f1f3f4] p-3">
      <div className="document-pages" style={{ zoom: scale }}>
        <div className="document-page-stack" aria-hidden="true">
          {Array.from({ length: pageCount }, (_, index) => (
            <div className="document-page" key={index} />
          ))}
        </div>
        <EditorContent className="document-editor-content" editor={editor} />
        {Array.from({ length: pageCount - 1 }, (_, index) => (
          <div
            className="document-page-mask"
            key={index}
            style={{ top: index * (PAGE_HEIGHT + PAGE_GAP) + PAGE_HEIGHT - PAGE_BOTTOM }}
            aria-hidden="true"
          />
        ))}
      </div>
    </div>
  );
}

type DocumentVersionHistoryProps = {
  documentId: string;
  ownerId: string;
  /** True until the collaboration service confirms history is available. */
  disabled?: boolean;
};

export function DocumentVersionHistory({ documentId, ownerId, disabled = false }: DocumentVersionHistoryProps) {
  const { getToken } = useAuth();
  const dialog = useRef<HTMLDialogElement>(null);
  const generation = useRef(0);
  const [versions, setVersions] = useState<VersionMetadata[]>([]);
  const [next, setNext] = useState(0);
  const [selected, setSelected] = useState<Version | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => () => { generation.current += 1; }, []);

  async function load(before = 0, revision?: number) {
    const attempt = ++generation.current;
    setBusy(true);
    setError("");
    try {
      const token = await getToken();
      if (!token) throw new Error("Sign in to view version history.");
      if (revision !== undefined) {
        const result = await getDocumentVersion(documentId, revision, token);
        if (generation.current === attempt) setSelected(result);
      } else {
        const result = await listDocumentVersions(documentId, token, before);
        if (generation.current === attempt) {
          setVersions((current) => before ? [...current, ...result.versions] : result.versions);
          setNext(result.nextBefore);
        }
      }
    } catch (cause) {
      if (generation.current === attempt) {
        setError(cause instanceof Error ? cause.message : "Could not load version history.");
      }
    } finally {
      if (generation.current === attempt) setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        disabled={disabled}
        aria-label="Version history"
        title={disabled ? "Version history is available once connected" : "Version history"}
        className="grid size-9 place-items-center rounded-full text-[#444746] hover:bg-[#e8eaed] disabled:cursor-not-allowed disabled:text-[#9aa0a6] disabled:hover:bg-transparent"
        onClick={() => {
          setSelected(null);
          setVersions([]);
          setNext(0);
          dialog.current?.showModal();
          void load();
        }}
      >
        <History aria-hidden="true" size={19} />
      </button>
      <dialog ref={dialog} aria-labelledby="version-history-title" className="m-auto max-h-[85vh] w-[min(1080px,95vw)] flex-col overflow-hidden rounded-xl bg-white p-5 shadow-xl backdrop:bg-black/30 open:flex"
        onClose={() => { generation.current += 1; setSelected(null); }}>
        <header className="flex shrink-0 items-center justify-between gap-4">
          <h2 id="version-history-title" className="text-lg font-semibold">Version history</h2>
          <button type="button" className="rounded px-3 py-2 hover:bg-gray-100" onClick={() => dialog.current?.close()}>Close</button>
        </header>
        {busy && <p role="status">Loading version history…</p>}
        {error && <p role="alert">{error} <button type="button" className="underline" onClick={() => void load()}>Retry</button></p>}
        <div className="mt-4 grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)_minmax(0,2fr)] gap-4 sm:grid-cols-[210px_1fr] sm:grid-rows-[minmax(0,1fr)]">
          <div className="min-h-0 overflow-y-auto pr-1 [scrollbar-width:thin]">
            <ul className="space-y-1">
              {versions.map((version) => (
                <li key={version.revision}>
                  <button type="button" aria-pressed={selected?.revision === version.revision}
                    className="w-full rounded p-2 text-left text-sm hover:bg-gray-100 aria-pressed:bg-blue-50"
                    onClick={() => void load(0, version.revision)}>
                    <span className="block font-medium">Version {version.revision} · {version.kind}</span>
                    <time dateTime={version.timestamp}>{new Date(version.timestamp).toLocaleString()}</time>
                    <span className="block break-all text-xs">{version.actorId === ownerId ? "You" : version.actorId}</span>
                  </button>
                </li>
              ))}
            </ul>
            {next > 0 && <button className="p-2 text-sm underline" type="button" disabled={busy} onClick={() => void load(next)}>Load older versions</button>}
          </div>
          {selected && <section aria-label="Version preview" className="min-h-0 overflow-y-auto">
            <h3 className="font-medium">{selected.title}</h3>
            <VersionPreview key={selected.revision} update={selected.update} />
          </section>}
        </div>
      </dialog>
    </>
  );
}
