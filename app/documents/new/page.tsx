import { auth } from "@clerk/nextjs/server";
import type { Metadata } from "next";
import { NewDocumentPrompt } from "./new-document-prompt";

export const metadata: Metadata = {
  title: "Untitled document",
};

/** Protects the explicit document creation action. */
export default async function NewDocumentPage() {
  await auth.protect();
  return <NewDocumentPrompt />;
}
