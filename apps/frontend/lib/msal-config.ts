import { Configuration, LogLevel } from "@azure/msal-browser";

/**
 * MSAL configuration for Entra ID (Azure AD) authentication.
 * Environment variables are injected at build time via Next.js.
 * Required vars (set in .env.local / App Service app settings):
 *   NEXT_PUBLIC_AZURE_CLIENT_ID   – Application (client) ID
 *   NEXT_PUBLIC_AZURE_TENANT_ID   – Directory (tenant) ID
 *   NEXT_PUBLIC_REDIRECT_URI      – Post-login redirect URI (e.g. http://localhost:3000)
 */

const clientId = process.env.NEXT_PUBLIC_AZURE_CLIENT_ID ?? "";
const tenantId = process.env.NEXT_PUBLIC_AZURE_TENANT_ID ?? "";
const redirectUri = process.env.NEXT_PUBLIC_REDIRECT_URI ?? (typeof window !== "undefined" ? window.location.origin : "");

export const msalConfig: Configuration = {
  auth: {
    clientId,
    authority: `https://login.microsoftonline.com/${tenantId}`,
    redirectUri,
    postLogoutRedirectUri: redirectUri,
  },
  cache: {
    cacheLocation: "sessionStorage",
    storeAuthStateInCookie: false,
  },
  system: {
    loggerOptions: {
      loggerCallback: (level, message, containsPii) => {
        if (containsPii) return;
        if (process.env.NODE_ENV !== "production") {
          switch (level) {
            case LogLevel.Error:
              console.error(message);
              break;
            case LogLevel.Warning:
              console.warn(message);
              break;
            case LogLevel.Info:
              console.info(message);
              break;
            case LogLevel.Verbose:
              console.debug(message);
              break;
          }
        }
      },
    },
  },
};

/** Scopes requested on login – openid/profile for basic identity */
export const loginRequest = {
  scopes: ["openid", "profile", "email"],
};

/** Silent token acquisition scopes for downstream API calls (extend as needed) */
export const apiRequest = {
  scopes: [
    process.env.NEXT_PUBLIC_API_SCOPE ?? "api://calabrio-wfm/.default",
  ],
};
