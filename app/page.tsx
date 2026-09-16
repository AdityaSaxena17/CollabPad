import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";

/** Routes authenticated visitors into the protected document workspace. */
export default async function Home() {
  await auth.protect();
  redirect("/documents");
}
