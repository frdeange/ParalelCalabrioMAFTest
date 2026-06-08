"use client";

import { useIsAuthenticated, useMsal } from "@azure/msal-react";
import { InteractionStatus } from "@azure/msal-browser";
import { useCallback } from "react";
import { loginRequest, apiRequest } from "@/lib/msal-config";

export interface AuthState {
  /** True when user has a valid MSAL account in the current session */
  isAuthenticated: boolean;
  /** True while MSAL is processing a redirect or popup interaction */
  isLoading: boolean;
  /** The active MSAL account (undefined before login) */
  account: ReturnType<typeof useMsal>["accounts"][0] | undefined;
  /** Trigger Entra ID redirect login */
  signIn: () => Promise<void>;
  /** Clear MSAL cache and redirect to post-logout URI */
  signOut: () => void;
  /**
   * Acquire an access token for the configured API scope silently;
   * falls back to loginRedirect if silent acquisition fails.
   */
  acquireToken: () => Promise<string | null>;
}

/**
 * Convenience hook that wraps useMsal/useIsAuthenticated into a stable
 * interface consumed across the app instead of reaching into MSAL directly.
 */
export function useAuth(): AuthState {
  const { instance, accounts, inProgress } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const account = accounts[0];

  const signIn = useCallback(async () => {
    await instance.loginRedirect(loginRequest);
  }, [instance]);

  const signOut = useCallback(() => {
    instance.logoutRedirect({ postLogoutRedirectUri: "/" });
  }, [instance]);

  const acquireToken = useCallback(async (): Promise<string | null> => {
    if (!account) return null;
    try {
      const response = await instance.acquireTokenSilent({
        ...apiRequest,
        account,
      });
      return response.accessToken;
    } catch {
      // Silent acquisition failed (e.g. expired session) — fall back to redirect
      await instance.loginRedirect({ ...apiRequest, ...loginRequest });
      return null;
    }
  }, [instance, account]);

  return {
    isAuthenticated,
    isLoading: inProgress !== InteractionStatus.None,
    account,
    signIn,
    signOut,
    acquireToken,
  };
}
