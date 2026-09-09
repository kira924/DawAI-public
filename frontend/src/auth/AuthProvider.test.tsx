import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TokenPair, UserProfile } from "../api/types";
import { AuthProvider } from "./AuthProvider";
import { useAuth } from "./useAuth";

const tokens: TokenPair = {
  access_token: "access-token",
  refresh_token: "refresh-token-value-that-is-long-enough",
  token_type: "bearer",
  expires_in: 900,
};

const profile: UserProfile = {
  id: 4,
  email: "manager@example.test",
  full_name: "Test Manager",
  role: "manager",
  tenant_id: 2,
  created_at: "2026-09-05T00:00:00Z",
};

function SessionProbe() {
  const { state, user } = useAuth();
  return <div><span>{state}</span><strong>{user?.full_name}</strong></div>;
}

describe("AuthProvider", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("restores a session by rotating the stored refresh token", async () => {
    window.sessionStorage.setItem("dawai.refreshToken", "stored-refresh-token");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/auth/refresh")) {
        return new Response(JSON.stringify(tokens), { status: 200 });
      }
      if (url.endsWith("/api/account/me")) {
        return new Response(JSON.stringify(profile), { status: 200 });
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { render } = await import("@testing-library/react");
    render(<AuthProvider><SessionProbe /></AuthProvider>);

    expect(await screen.findByText("Test Manager")).toBeInTheDocument();
    expect(screen.getByText("authenticated")).toBeInTheDocument();
    expect(window.sessionStorage.getItem("dawai.refreshToken")).toBe(tokens.refresh_token);
  });

  it("falls back to an anonymous state when no session exists", async () => {
    const { render } = await import("@testing-library/react");
    render(<AuthProvider><SessionProbe /></AuthProvider>);
    expect(await screen.findByText("anonymous")).toBeInTheDocument();
  });
});
