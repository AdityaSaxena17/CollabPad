import { auth } from "@clerk/nextjs/server";
import type { Metadata } from "next";
import { DocumentsDashboard } from "./documents-dashboard";
import { sampleDocuments } from "@/lib/documents";

export const metadata: Metadata = {
  title: "Documents",
};

/** Protects and renders the local document dashboard. */
export default async function DocumentsPage() {
  await auth.protect();
  return <DocumentsDashboard documents={sampleDocuments} />;
}
