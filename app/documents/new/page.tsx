import { auth } from "@clerk/nextjs/server";
import type { Metadata } from "next";
import { DocumentEditor } from "@/components/document-editor";
import { blankDocumentContent } from "@/lib/documents";

export const metadata: Metadata = {
  title: "Untitled document",
};

/** Protects and opens a blank, non-persisted document draft. */
export default async function NewDocumentPage() {
  await auth.protect();

  return (
    <DocumentEditor
      document={{
        id: "new",
        title: "Untitled document",
        updatedLabel: "Local draft",
        content: blankDocumentContent,
      }}
    />
  );
}
