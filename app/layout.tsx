import { ClerkProvider } from "@clerk/nextjs";
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Collab Docs",
    template: "%s | Collab Docs",
  },
  description: "A focused workspace for collaborative documents.",
};

/** Provides global styling and Clerk session context for every route. */
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">
        <ClerkProvider>{children}</ClerkProvider>
      </body>
    </html>
  );
}
