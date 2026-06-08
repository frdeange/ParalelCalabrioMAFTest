"use client";

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

  if (inProgress !== InteractionStatus.None) {
    return (
      fallback ?? (
        <div className="flex min-h-screen items-center justify-center">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-600 border-t-transparent" />
        </div>
      )
    );
  }

  if (!isAuthenticated) {
    // Trigger redirect login; component renders nothing while redirecting
    instance.loginRedirect(loginRequest).catch(console.error);
    return null;
  }

  return <>{children}</>;
}
