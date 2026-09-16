import { SignIn } from "@clerk/nextjs";
import Link from "next/link";
import { CollabMark } from "@/components/collab-mark";

/** Hosts Clerk's sign-in flow inside the Collab Docs shell. */
export default function SignInPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-[radial-gradient(circle_at_top,#e8f0fe_0,transparent_42%),#f8fafd] px-4 py-10">
      <Link
        href="/"
        aria-label="Collab Docs home"
        className="mb-8 rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-[#1a73e8] focus-visible:ring-offset-4"
      >
        <CollabMark />
      </Link>
      <SignIn />
      <p className="mt-6 max-w-md text-center text-sm leading-6 text-[#5f6368]">
        Sign in to open your document workspace. Your content remains local until
        the collaboration backend is connected.
      </p>
    </main>
  );
}
