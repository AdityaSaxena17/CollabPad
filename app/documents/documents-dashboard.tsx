"use client";

import { UserButton } from "@clerk/nextjs";
import {
  Clock3,
  FilePlus2,
  FileText,
  LayoutGrid,
  Search,
  ServerOff,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { CollabMark } from "@/components/collab-mark";
import type { DocumentViewModel } from "@/lib/documents";

type DocumentsDashboardProps = {
  documents: DocumentViewModel[];
};

const previewAccents = [
  "from-[#d2e3fc] to-[#e8f0fe]",
  "from-[#fce8e6] to-[#fef2f1]",
  "from-[#e6f4ea] to-[#f0f8f2]",
];

/** Displays the searchable local document workspace for the signed-in user. */
export function DocumentsDashboard({ documents }: DocumentsDashboardProps) {
  const [query, setQuery] = useState("");
  const filteredDocuments = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();

    if (!normalizedQuery) {
      return documents;
    }

    return documents.filter((document) =>
      document.title.toLocaleLowerCase().includes(normalizedQuery),
    );
  }, [documents, query]);

  return (
    <main className="min-h-screen bg-[#f8fafd] text-[#202124]">
      <header className="sticky top-0 z-20 border-b border-[#e4e7eb] bg-white/95 backdrop-blur">
        <div className="mx-auto flex h-[72px] max-w-[1280px] items-center gap-4 px-4 sm:px-6">
          <Link
            href="/documents"
            aria-label="Collab Docs documents"
            className="shrink-0 rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-[#1a73e8] focus-visible:ring-offset-2"
          >
            <span className="hidden sm:inline-flex">
              <CollabMark />
            </span>
            <span className="sm:hidden">
              <CollabMark compact />
            </span>
          </Link>

          <label className="relative mx-auto block w-full max-w-[720px]">
            <span className="sr-only">Search documents</span>
            <Search
              aria-hidden="true"
              size={21}
              className="absolute top-1/2 left-4 -translate-y-1/2 text-[#5f6368]"
            />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search documents"
              className="h-12 w-full rounded-3xl border border-transparent bg-[#f1f3f4] pr-4 pl-12 text-base outline-none transition focus:border-[#a8c7fa] focus:bg-white focus:shadow-sm"
            />
          </label>

          <span
            title="Backend not connected"
            className="hidden items-center gap-2 rounded-full bg-[#fef7e0] px-3 py-2 text-xs font-medium whitespace-nowrap text-[#735c0f] lg:flex"
          >
            <ServerOff aria-hidden="true" size={15} />
            Local preview
          </span>
          <UserButton />
        </div>
      </header>

      <section className="border-b border-[#e0e3e7] bg-[#f1f3f4]">
        <div className="mx-auto max-w-[1080px] px-5 py-8 sm:px-8 sm:py-10">
          <div className="mb-5 flex items-center justify-between">
            <div>
              <h1 className="text-lg font-medium text-[#202124]">Start a new document</h1>
              <p className="mt-1 text-sm text-[#5f6368]">
                Draft locally now and connect persistence later.
              </p>
            </div>
            <span className="hidden text-sm text-[#5f6368] sm:inline">Template gallery</span>
          </div>

          <Link
            href="/documents/new"
            className="group block w-[152px] rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-[#1a73e8] focus-visible:ring-offset-4 focus-visible:ring-offset-[#f1f3f4]"
          >
            <span className="grid aspect-[0.77] place-items-center rounded-md border border-[#dadce0] bg-white shadow-sm transition group-hover:border-[#1a73e8] group-hover:shadow-md">
              <FilePlus2
                aria-hidden="true"
                size={40}
                strokeWidth={1.6}
                className="text-[#1a73e8]"
              />
            </span>
            <span className="mt-3 block text-sm font-medium">Blank document</span>
          </Link>
        </div>
      </section>

      <section className="mx-auto max-w-[1080px] px-5 py-8 sm:px-8 sm:py-10">
        <div className="mb-6 flex items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-medium">Recent documents</h2>
            <p className="mt-1 text-sm text-[#5f6368]">
              Sample documents reset when the page is refreshed.
            </p>
          </div>
          <span className="flex size-9 items-center justify-center rounded-full bg-[#e8f0fe] text-[#1967d2]">
            <LayoutGrid aria-hidden="true" size={18} />
          </span>
        </div>

        {filteredDocuments.length > 0 ? (
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {filteredDocuments.map((document, index) => {
              const accent =
                previewAccents[index % previewAccents.length] ??
                "from-[#d2e3fc] to-[#e8f0fe]";

              return (
                <Link
                  key={document.id}
                  href={`/documents/${document.id}`}
                  className="group overflow-hidden rounded-lg border border-[#dadce0] bg-white outline-none transition hover:-translate-y-0.5 hover:border-[#a8c7fa] hover:shadow-md focus-visible:ring-2 focus-visible:ring-[#1a73e8] focus-visible:ring-offset-3"
                >
                  <span
                    className={`relative block aspect-[1.38] overflow-hidden border-b border-[#e0e3e7] bg-gradient-to-br ${accent} p-5`}
                  >
                    <span className="mx-auto block h-full max-w-[170px] rounded-sm bg-white p-5 shadow-sm">
                      <span className="mb-3 block h-2 w-2/3 rounded bg-[#3c4043]/75" />
                      <span className="mb-2 block h-1.5 w-full rounded bg-[#9aa0a6]/45" />
                      <span className="mb-2 block h-1.5 w-5/6 rounded bg-[#9aa0a6]/45" />
                      <span className="mb-5 block h-1.5 w-11/12 rounded bg-[#9aa0a6]/45" />
                      <span className="mb-2 block h-1.5 w-1/2 rounded bg-[#1a73e8]/40" />
                      <span className="block h-1.5 w-4/5 rounded bg-[#9aa0a6]/40" />
                    </span>
                  </span>
                  <span className="flex items-start gap-3 p-4">
                    <FileText
                      aria-hidden="true"
                      size={20}
                      className="mt-0.5 shrink-0 text-[#1a73e8]"
                    />
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium text-[#202124]">
                        {document.title}
                      </span>
                      <span className="mt-1 flex items-center gap-1.5 text-xs text-[#5f6368]">
                        <Clock3 aria-hidden="true" size={13} />
                        {document.updatedLabel}
                      </span>
                    </span>
                  </span>
                </Link>
              );
            })}
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-[#c7cacf] bg-white px-6 py-14 text-center">
            <Search aria-hidden="true" size={28} className="mx-auto mb-3 text-[#9aa0a6]" />
            <p className="font-medium">No documents match “{query.trim()}”</p>
            <button
              type="button"
              onClick={() => setQuery("")}
              className="mt-3 rounded-full px-4 py-2 text-sm font-medium text-[#1967d2] hover:bg-[#e8f0fe] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1a73e8]"
            >
              Clear search
            </button>
          </div>
        )}
      </section>
    </main>
  );
}
