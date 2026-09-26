import { auth } from "@clerk/nextjs/server";
import type { Metadata } from "next";
import { DocumentsDashboard } from "./documents-dashboard";

export const metadata: Metadata = {
  title: "Documents",
};

/** Protects and renders the saved document dashboard. */
export default async function DocumentsPage() {
  await auth.protect();
  return <DocumentsDashboard />;
}
