import { auth } from "@clerk/nextjs/server";
import { CollaborativeDocument } from "@/components/collaborative-document";

type DocumentPageProps = {
  params: Promise<{ documentId: string }>;
};

/** Protects the editor route; the Python service authorizes document access. */
export default async function DocumentPage(props: DocumentPageProps) {
  await auth.protect();
  const { documentId } = await props.params;
  return <CollaborativeDocument documentId={documentId} />;
}
