"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useIsAuthenticated } from "@azure/msal-react";

/**
 * Root route: redirect authenticated users to /chat, everyone else to /login.
 */
export default function RootPage() {
  const router = useRouter();
  const isAuthenticated = useIsAuthenticated();

  useEffect(() => {
    if (isAuthenticated) {
      router.replace("/chat");
    } else {
      router.replace("/login");
    }
  }, [isAuthenticated, router]);

  return null;
}
