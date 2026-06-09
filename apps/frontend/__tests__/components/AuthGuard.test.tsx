import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { AuthGuard } from "@/components/auth/AuthGuard";

const instance = {
  loginRedirect: vi.fn().mockResolvedValue(undefined),
};

const msalState = {
  inProgress: "none",
  isAuthenticated: false,
};

vi.mock("@azure/msal-react", () => ({
  useMsal: () => ({ instance, inProgress: msalState.inProgress }),
  useIsAuthenticated: () => msalState.isAuthenticated,
}));

vi.mock("@azure/msal-browser", () => ({
  InteractionStatus: { None: "none" },
}));

vi.mock("@/lib/msal-config", () => ({
  loginRequest: { scopes: ["openid", "profile", "email"] },
}));

beforeEach(() => {
  msalState.inProgress = "none";
  msalState.isAuthenticated = false;
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("AuthGuard", () => {
  it("renders children when the user is authenticated", () => {
    msalState.isAuthenticated = true;
    render(
      <AuthGuard>
        <p>secret content</p>
      </AuthGuard>,
    );
    expect(screen.getByText("secret content")).toBeInTheDocument();
  });

  it("shows the default spinner fallback while unauthenticated", () => {
    const { container } = render(
      <AuthGuard>
        <p>secret content</p>
      </AuthGuard>,
    );
    expect(screen.queryByText("secret content")).not.toBeInTheDocument();
    expect(container.querySelector(".animate-spin")).toBeInTheDocument();
  });

  it("renders a custom fallback when provided", () => {
    render(
      <AuthGuard fallback={<p>loading…</p>}>
        <p>secret content</p>
      </AuthGuard>,
    );
    expect(screen.getByText("loading…")).toBeInTheDocument();
    expect(screen.queryByText("secret content")).not.toBeInTheDocument();
  });

  it("shows the fallback (not children) while an interaction is in progress", () => {
    msalState.inProgress = "login";
    msalState.isAuthenticated = false;
    render(
      <AuthGuard>
        <p>secret content</p>
      </AuthGuard>,
    );
    expect(screen.queryByText("secret content")).not.toBeInTheDocument();
  });

  it("redirects to login when unauthenticated and idle", async () => {
    render(
      <AuthGuard>
        <p>secret content</p>
      </AuthGuard>,
    );
    await waitFor(() => {
      expect(instance.loginRedirect).toHaveBeenCalledWith({
        scopes: ["openid", "profile", "email"],
      });
    });
  });

  it("does not redirect while an interaction is in progress", () => {
    msalState.inProgress = "login";
    render(
      <AuthGuard>
        <p>secret content</p>
      </AuthGuard>,
    );
    expect(instance.loginRedirect).not.toHaveBeenCalled();
  });

  it("does not redirect when already authenticated", () => {
    msalState.isAuthenticated = true;
    render(
      <AuthGuard>
        <p>secret content</p>
      </AuthGuard>,
    );
    expect(instance.loginRedirect).not.toHaveBeenCalled();
  });
});
