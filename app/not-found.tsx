import { FileQuestion } from "lucide-react";
import Link from "next/link";
import { CollabMark } from "@/components/collab-mark";

/** Explains when a requested document is not part of the local preview. */
export default function NotFound() {
  return (
    <main className="grid min-h-screen place-items-center bg-[#f8fafd] px-5">
      <section className="w-full max-w-md rounded-2xl border border-[#e0e3e7] bg-white p-8 text-center shadow-sm">
        <span className="mb-8 inline-flex">
          <CollabMark />
        </span>
        <FileQuestion aria-hidden="true" size={46} className="mx-auto text-[#9aa0a6]" />
        <h1 className="mt-5 text-2xl font-medium">Document not found</h1>
        <p className="mt-3 text-sm leading-6 text-[#5f6368]">
          This local preview only contains the sample documents shown in your workspace.
        </p>
        <Link
          href="/documents"
          className="mt-7 inline-flex rounded-full bg-[#1a73e8] px-5 py-2.5 text-sm font-medium text-white hover:bg-[#185abc] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#1a73e8] focus-visible:ring-offset-3"
        >
          Back to documents
        </Link>
      </section>
    </main>
  );
}
