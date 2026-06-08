"use client";

import { useEffect, useRef } from "react";
import { useIsAuthenticated, useMsal } from "@azure/msal-react";
import { InteractionStatus } from "@azure/msal-browser";
import { loginRequest } from "@/lib/msal-config";

interface AuthGuardProps {
  children: React.ReactNode;
  /** Optional fallback rendered while MSAL determines auth state */
  fallback?: React.ReactNode;
}

/**
 * AuthGuard — replaces the old ProtectedRoute.
 * Redirects unauthenticated users to /login instead of triggering loginRedirect
 * directly, keeping the login UX consistent and avoiding unexpected Entra ID
 * redirects from deep routes.
 */
export function AuthGuard({ children, fallback }: AuthGuardProps) {
  const { instance, inProgress } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const redirectTriggered = useRef(false);

  // Defer loginRedirect to after render to avoid calling it during render
  // (which could cause double-invocation in React Strict Mode).
  useEffect(() => {
    if (
      !isAuthenticated &&
      inProgress === InteractionStatus.None &&
      !redirectTriggered.current
    ) {
      redirectTriggered.current = true;
      instance.loginRedirect(loginRequest).catch(console.error);
    }
  }, [isAuthenticated, inProgress, instance]);

  if (inProgress !== InteractionStatus.None || !isAuthenticated) {
    return (
      fallback ?? (
        <div className="flex min-h-screen items-center justify-center">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-600 border-t-transparent" />
        </div>
      )
    );
  }

  return <>{children}</>;
}
