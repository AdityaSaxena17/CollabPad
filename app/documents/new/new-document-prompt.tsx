"use client";

import { useAuth } from "@clerk/nextjs";
import { FilePlus2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { createDocument } from "@/lib/gateway";

/** Creates a document only after an explicit click, avoiding duplicate GET-side writes. */
export function NewDocumentPrompt() {
  const { getToken } = useAuth();
  const router = useRouter();
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  async function create() {
    if (creating) return;
    setCreating(true);
    setError("");
    try {
      const token = await getToken();
      if (!token) throw new Error("Sign in before creating a document.");
      const document = await createDocument(token);
      router.replace(`/documents/${document.id}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create document.");
      setCreating(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-[#f8fafd] px-4 text-[#202124]">
      <section className="w-full max-w-md rounded-2xl border border-[#dadce0] bg-white p-8 text-center shadow-sm">
        <FilePlus2 aria-hidden="true" size={42} className="mx-auto mb-4 text-[#1a73e8]" />
        <h1 className="text-xl font-medium">Create a blank document</h1>
        <p className="mt-2 text-sm text-[#5f6368]">Your document will be saved and private until you enable sharing.</p>
        {error && <p role="alert" className="mt-4 text-sm text-[#b3261e]">{error}</p>}
        <button type="button" onClick={() => void create()} disabled={creating} className="mt-6 rounded-full bg-[#1a73e8] px-6 py-2.5 text-sm font-medium text-white disabled:opacity-60">
          {creating ? "Creating…" : "Create document"}
        </button>
        <Link href="/documents" className="mt-4 block text-sm text-[#1967d2]">Back to documents</Link>
      </section>
    </main>
  );
}
