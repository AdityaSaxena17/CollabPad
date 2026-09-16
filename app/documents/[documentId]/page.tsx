import { auth } from "@clerk/nextjs/server";
import { notFound } from "next/navigation";
import { DocumentEditor } from "@/components/document-editor";
import { findSampleDocument } from "@/lib/documents";

type DocumentPageProps = {
  params: Promise<{ documentId: string }>;
};

/** Protects and opens a known local sample document. */
export default async function DocumentPage(props: DocumentPageProps) {
  await auth.protect();
  const { documentId } = await props.params;
  const document = findSampleDocument(documentId);

  if (!document) {
    notFound();
  }

  return <DocumentEditor document={document} />;
}
