"use client";

import { MsalProvider as AzureMsalProvider } from "@azure/msal-react";
import { PublicClientApplication } from "@azure/msal-browser";
import { msalConfig } from "@/lib/msal-config";

/**
 * Singleton MSAL PublicClientApplication instance.
 * Created once at module load; safe for SSR because msal-browser
 * skips initialisation on the server.
 */
const msalInstance = new PublicClientApplication(msalConfig);

interface MsalProviderProps {
  children: React.ReactNode;
}

/**
 * Wraps the application with the MSAL React context.
 * Place this as high as possible in the component tree (e.g. RootLayout).
 */
export function MsalProvider({ children }: MsalProviderProps) {
  return (
    <AzureMsalProvider instance={msalInstance}>
      {children}
    </AzureMsalProvider>
  );
}
