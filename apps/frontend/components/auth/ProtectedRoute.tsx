"use client";

import { useEffect, useRef } from "react";
import { useIsAuthenticated, useMsal } from "@azure/msal-react";
import { InteractionStatus } from "@azure/msal-browser";
import { loginRequest } from "@/lib/msal-config";

interface ProtectedRouteProps {
  children: React.ReactNode;
  /** Optional fallback rendered while MSAL determines auth state */
  fallback?: React.ReactNode;
}

/**
 * Higher-order component that redirects unauthenticated users to Entra ID login.
 * Renders `fallback` (or a spinner) while the interaction is in progress.
 *
 * Usage:
 *   <ProtectedRoute>
 *     <YourPage />
 *   </ProtectedRoute>
 */
export function ProtectedRoute({ children, fallback }: ProtectedRouteProps) {
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

  if (inProgress !== InteractionStatus.None || (!isAuthenticated && !redirectTriggered.current)) {
    return (
      fallback ?? (
        <div className="flex min-h-screen items-center justify-center">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-600 border-t-transparent" />
        </div>
      )
    );
  }

  if (!isAuthenticated) {
    return null;
  }

  return <>{children}</>;
}
