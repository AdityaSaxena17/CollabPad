"use client";

import { UserButton, useAuth } from "@clerk/nextjs";
import { Clock3, FilePlus2, FileText, Search } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { CollabMark } from "@/components/collab-mark";
import { listDocuments, type DocumentRecord } from "@/lib/gateway";

const previewAccents = [
  "from-[#d2e3fc] to-[#e8f0fe]",
  "from-[#fce8e6] to-[#fef2f1]",
  "from-[#e6f4ea] to-[#f0f8f2]",
];

/** Lists only documents actually saved for the current Clerk user. */
export function DocumentsDashboard() {
  const { getToken, isLoaded } = useAuth();
  const [query, setQuery] = useState("");
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isLoaded) return;
    let cancelled = false;

    async function load() {
      try {
        const token = await getToken();
        if (!token) throw new Error("Sign in to see your documents.");
        const saved = await listDocuments(token);
        if (!cancelled) {
          setDocuments(saved);
          setError("");
        }
      } catch (cause) {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "Could not load documents.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => { cancelled = true; };
  }, [getToken, isLoaded]);

  const filteredDocuments = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return normalizedQuery
      ? documents.filter((document) =>
          document.title.toLocaleLowerCase().includes(normalizedQuery),
        )
      : documents;
  }, [documents, query]);

  return (
    <main className="min-h-screen bg-[#f8fafd] text-[#202124]">
      <header className="sticky top-0 z-20 border-b border-[#e4e7eb] bg-white/95 backdrop-blur">
        <div className="mx-auto flex h-[72px] max-w-[1280px] items-center gap-4 px-4 sm:px-6">
          <Link href="/documents" aria-label="Collab Docs documents" className="shrink-0 rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-[#1a73e8]">
            <span className="hidden sm:inline-flex"><CollabMark /></span>
            <span className="sm:hidden"><CollabMark compact /></span>
          </Link>
          <label className="relative mx-auto block w-full max-w-[720px]">
            <span className="sr-only">Search documents</span>
            <Search aria-hidden="true" size={21} className="absolute top-1/2 left-4 -translate-y-1/2 text-[#5f6368]" />
            <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search documents" className="h-12 w-full rounded-3xl border border-transparent bg-[#f1f3f4] pr-4 pl-12 text-base outline-none transition focus:border-[#a8c7fa] focus:bg-white focus:shadow-sm" />
          </label>
          <UserButton />
        </div>
      </header>

      <section className="border-b border-[#e0e3e7] bg-[#f1f3f4]">
        <div className="mx-auto max-w-[1080px] px-5 py-8 sm:px-8 sm:py-10">
          <h1 className="mb-2 text-lg font-medium">Start a new document</h1>
          <p className="mb-5 text-sm text-[#5f6368]">Create a saved document you can share with other editors.</p>
          <Link href="/documents/new" className="group block w-[152px] rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-[#1a73e8] focus-visible:ring-offset-4 focus-visible:ring-offset-[#f1f3f4]">
            <span className="grid aspect-[0.77] place-items-center rounded-md border border-[#dadce0] bg-white shadow-sm transition group-hover:border-[#1a73e8] group-hover:shadow-md">
              <FilePlus2 aria-hidden="true" size={40} strokeWidth={1.6} className="text-[#1a73e8]" />
            </span>
            <span className="mt-3 block text-sm font-medium">Blank document</span>
          </Link>
        </div>
      </section>

      <section className="mx-auto max-w-[1080px] px-5 py-8 sm:px-8 sm:py-10">
        <h2 className="mb-6 text-lg font-medium">Recent documents</h2>
        {loading ? (
          <p role="status" className="text-sm text-[#5f6368]">Loading documents…</p>
        ) : error ? (
          <div role="alert" className="rounded-xl border border-[#f0d58c] bg-[#fef7e0] p-6 text-sm text-[#735c0f]">{error} Refresh to try again.</div>
        ) : filteredDocuments.length > 0 ? (
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {filteredDocuments.map((document, index) => (
              <Link key={document.id} href={`/documents/${document.id}`} className="group overflow-hidden rounded-lg border border-[#dadce0] bg-white outline-none transition hover:-translate-y-0.5 hover:border-[#a8c7fa] hover:shadow-md focus-visible:ring-2 focus-visible:ring-[#1a73e8] focus-visible:ring-offset-3">
                <span className={`relative block aspect-[1.38] overflow-hidden border-b border-[#e0e3e7] bg-gradient-to-br ${previewAccents[index % previewAccents.length]} p-5`}>
                  <span className="mx-auto block h-full max-w-[170px] rounded-sm bg-white p-5 shadow-sm">
                    <span className="mb-3 block h-2 w-2/3 rounded bg-[#3c4043]/75" />
                    <span className="mb-2 block h-1.5 w-full rounded bg-[#9aa0a6]/45" />
                    <span className="mb-2 block h-1.5 w-5/6 rounded bg-[#9aa0a6]/45" />
                    <span className="block h-1.5 w-11/12 rounded bg-[#9aa0a6]/45" />
                  </span>
                </span>
                <span className="flex items-start gap-3 p-4">
                  <FileText aria-hidden="true" size={20} className="mt-0.5 shrink-0 text-[#1a73e8]" />
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium">{document.title}</span>
                    <span className="mt-1 flex items-center gap-1.5 text-xs text-[#5f6368]"><Clock3 aria-hidden="true" size={13} />Updated {new Date(document.updatedAt).toLocaleString()}</span>
                  </span>
                </span>
              </Link>
            ))}
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-[#c7cacf] bg-white px-6 py-14 text-center">
            <FileText aria-hidden="true" size={28} className="mx-auto mb-3 text-[#9aa0a6]" />
            <p className="font-medium">{query.trim() ? "No documents match your search" : "No saved documents yet"}</p>
            <p className="mt-2 text-sm text-[#5f6368]">{query.trim() ? "Try another title." : "Create a blank document to get started."}</p>
          </div>
        )}
      </section>
    </main>
  );
}
