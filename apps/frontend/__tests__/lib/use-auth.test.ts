import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";

import { useAuth } from "@/lib/use-auth";

// --- MSAL mocks ---------------------------------------------------------

const instance = {
  loginRedirect: vi.fn().mockResolvedValue(undefined),
  logoutRedirect: vi.fn(),
  acquireTokenSilent: vi.fn(),
  acquireTokenRedirect: vi.fn().mockResolvedValue(undefined),
};

const msalState = {
  accounts: [] as Array<{ username: string }>,
  inProgress: "none",
  isAuthenticated: false,
};

vi.mock("@azure/msal-react", () => ({
  useMsal: () => ({
    instance,
    accounts: msalState.accounts,
    inProgress: msalState.inProgress,
  }),
  useIsAuthenticated: () => msalState.isAuthenticated,
}));

vi.mock("@azure/msal-browser", () => ({
  InteractionStatus: { None: "none" },
}));

vi.mock("@/lib/msal-config", () => ({
  loginRequest: { scopes: ["openid", "profile", "email"] },
  apiRequest: { scopes: ["api://calabrio-wfm/.default"] },
}));

beforeEach(() => {
  msalState.accounts = [];
  msalState.inProgress = "none";
  msalState.isAuthenticated = false;
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("useAuth", () => {
  it("reports unauthenticated state with no account", () => {
    const { result } = renderHook(() => useAuth());
    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.account).toBeUndefined();
    expect(result.current.isLoading).toBe(false);
  });

  it("reports loading while an interaction is in progress", () => {
    msalState.inProgress = "login";
    const { result } = renderHook(() => useAuth());
    expect(result.current.isLoading).toBe(true);
  });

  it("exposes the active account and authenticated state", () => {
    msalState.accounts = [{ username: "user@contoso.com" }];
    msalState.isAuthenticated = true;
    const { result } = renderHook(() => useAuth());
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.account).toEqual({ username: "user@contoso.com" });
  });

  it("signIn triggers a login redirect with the login request", async () => {
    const { result } = renderHook(() => useAuth());
    await act(async () => {
      await result.current.signIn();
    });
    expect(instance.loginRedirect).toHaveBeenCalledWith({
      scopes: ["openid", "profile", "email"],
    });
  });

  it("signOut triggers a logout redirect to the root", () => {
    const { result } = renderHook(() => useAuth());
    act(() => {
      result.current.signOut();
    });
    expect(instance.logoutRedirect).toHaveBeenCalledWith({
      postLogoutRedirectUri: "/",
    });
  });

  it("acquireToken returns null when there is no account", async () => {
    const { result } = renderHook(() => useAuth());
    const token = await result.current.acquireToken();
    expect(token).toBeNull();
    expect(instance.acquireTokenSilent).not.toHaveBeenCalled();
  });

  it("acquireToken returns the silent access token when available", async () => {
    msalState.accounts = [{ username: "user@contoso.com" }];
    instance.acquireTokenSilent.mockResolvedValueOnce({
      accessToken: "silent-token",
    });
    const { result } = renderHook(() => useAuth());
    const token = await result.current.acquireToken();
    expect(token).toBe("silent-token");
    expect(instance.acquireTokenSilent).toHaveBeenCalledWith({
      scopes: ["api://calabrio-wfm/.default"],
      account: { username: "user@contoso.com" },
    });
  });

  it("acquireToken falls back to interactive redirect on silent failure", async () => {
    msalState.accounts = [{ username: "user@contoso.com" }];
    instance.acquireTokenSilent.mockRejectedValueOnce(new Error("expired"));
    const { result } = renderHook(() => useAuth());
    const token = await result.current.acquireToken();
    expect(token).toBeNull();
    // Spreads apiRequest last so the API scopes win over login scopes.
    expect(instance.acquireTokenRedirect).toHaveBeenCalledWith({
      scopes: ["api://calabrio-wfm/.default"],
      account: { username: "user@contoso.com" },
    });
  });
});
