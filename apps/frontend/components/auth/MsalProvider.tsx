"use client";

import { MsalProvider as AzureMsalProvider } from "@azure/msal-react";
import { PublicClientApplication } from "@azure/msal-browser";
import { msalConfig } from "@/lib/msal-config";

/**
 * Singleton MSAL PublicClientApplication instance.
 * This file is a client component ("use client"), so it only runs in the browser.
 * The PCA is created once at module load time; MSAL skips initialisation on the
 * server because the module itself is never imported in a server context.
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
