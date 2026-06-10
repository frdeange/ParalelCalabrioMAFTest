/**
 * MSAL auth-seeding fixture for the smoke e2e (issue #30).
 *
 * Instead of driving a real Entra ID redirect (which would require live
 * credentials and network access), we seed MSAL's sessionStorage token cache
 * directly so the app boots already authenticated. MSAL's `createKeyMaps`
 * migration rebuilds its internal `msal.account.keys` / `msal.token.keys.*`
 * indexes from the raw entities we write, so we only need to provide:
 *   - one Account entity
 *   - one IdToken credential
 *   - one (non-expired) AccessToken credential matching the API scope
 *
 * Cache key schema (verified against @azure/msal-common 14.x):
 *   Account:     <homeAccountId>-<environment>-<realm>
 *   IdToken:     <homeAccountId>-<environment>-idtoken-<clientId>-<realm>---
 *   AccessToken: <homeAccountId>-<environment>-accesstoken-<clientId>-<realm>-<target>--
 * (all lower-cased, "-" separated; trailing empty key components are kept).
 */

import type { Page } from "@playwright/test";

// These MUST match the NEXT_PUBLIC_* build env in playwright.config.ts.
export const E2E_CLIENT_ID = "e2e-client-id";
export const E2E_TENANT_ID = "e2e-tenant-id";
export const E2E_API_SCOPE = "api://calabrio-wfm/.default";

// Public-cloud preferred cache environment. MSAL's hard-coded metadata aliases
// login.microsoftonline.com <-> login.windows.net, so authority matching works
// without any network instance-discovery call.
const ENVIRONMENT = "login.windows.net";

const UID = "e2e-uid";
const HOME_ACCOUNT_ID = `${UID}.${E2E_TENANT_ID}`;
const USERNAME = "e2e.user@calabrio.test";
const DISPLAY_NAME = "E2E User";

/** base64url-encode (no padding) — used for the fake id_token. */
function b64url(input: string): string {
  return Buffer.from(input, "utf8")
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

/** Build an unsigned id_token JWT. MSAL parses claims but does not verify the signature when reading from cache. */
function buildIdToken(nowSeconds: number): string {
  const header = b64url(JSON.stringify({ typ: "JWT", alg: "none" }));
  const payload = b64url(
    JSON.stringify({
      iss: `https://login.microsoftonline.com/${E2E_TENANT_ID}/v2.0`,
      sub: UID,
      oid: UID,
      tid: E2E_TENANT_ID,
      preferred_username: USERNAME,
      name: DISPLAY_NAME,
      aud: E2E_CLIENT_ID,
      ver: "2.0",
      iat: nowSeconds,
      nbf: nowSeconds,
      exp: nowSeconds + 86_400,
    })
  );
  return `${header}.${payload}.e2e-signature`;
}

/**
 * Build the full record of sessionStorage entries that put MSAL into an
 * authenticated state with a cached access token for the API scope.
 */
export function buildMsalCacheEntries(): Record<string, string> {
  const now = Math.floor(Date.now() / 1000);
  const expiresOn = now + 86_400;

  const idToken = buildIdToken(now);
  const clientInfo = b64url(JSON.stringify({ uid: UID, utid: E2E_TENANT_ID }));

  const idTokenClaims = {
    iss: `https://login.microsoftonline.com/${E2E_TENANT_ID}/v2.0`,
    oid: UID,
    sub: UID,
    tid: E2E_TENANT_ID,
    preferred_username: USERNAME,
    name: DISPLAY_NAME,
    ver: "2.0",
  };

  const accountKey =
    `${HOME_ACCOUNT_ID}-${ENVIRONMENT}-${E2E_TENANT_ID}`.toLowerCase();
  const idTokenKey =
    `${HOME_ACCOUNT_ID}-${ENVIRONMENT}-idtoken-${E2E_CLIENT_ID}-${E2E_TENANT_ID}---`.toLowerCase();
  const accessTokenKey =
    `${HOME_ACCOUNT_ID}-${ENVIRONMENT}-accesstoken-${E2E_CLIENT_ID}-${E2E_TENANT_ID}-${E2E_API_SCOPE}--`.toLowerCase();

  const account = {
    homeAccountId: HOME_ACCOUNT_ID,
    environment: ENVIRONMENT,
    realm: E2E_TENANT_ID,
    localAccountId: UID,
    username: USERNAME,
    name: DISPLAY_NAME,
    authorityType: "MSSTS",
    clientInfo,
    idTokenClaims,
    tenantProfiles: [
      {
        tenantId: E2E_TENANT_ID,
        localAccountId: UID,
        name: DISPLAY_NAME,
        isHomeTenant: true,
      },
    ],
  };

  const idTokenEntity = {
    credentialType: "IdToken",
    homeAccountId: HOME_ACCOUNT_ID,
    environment: ENVIRONMENT,
    clientId: E2E_CLIENT_ID,
    secret: idToken,
    realm: E2E_TENANT_ID,
  };

  const accessTokenEntity = {
    credentialType: "AccessToken",
    homeAccountId: HOME_ACCOUNT_ID,
    environment: ENVIRONMENT,
    clientId: E2E_CLIENT_ID,
    secret: "e2e-access-token",
    realm: E2E_TENANT_ID,
    target: E2E_API_SCOPE,
    cachedAt: now.toString(),
    expiresOn: expiresOn.toString(),
    extendedExpiresOn: expiresOn.toString(),
    tokenType: "Bearer",
  };

  return {
    [accountKey]: JSON.stringify(account),
    [idTokenKey]: JSON.stringify(idTokenEntity),
    [accessTokenKey]: JSON.stringify(accessTokenEntity),
  };
}

/**
 * Seed the MSAL session cache so the app loads authenticated.
 * Uses addInitScript so the entries exist before any app script runs, on every
 * navigation within the test.
 */
export async function seedAuthenticatedSession(page: Page): Promise<void> {
  const entries = buildMsalCacheEntries();
  await page.addInitScript((data: Record<string, string>) => {
    for (const [key, value] of Object.entries(data)) {
      window.sessionStorage.setItem(key, value);
    }
  }, entries);
}
