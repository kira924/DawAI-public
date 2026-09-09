import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { ApiError, requestJson } from "../api/client";
import type { TokenPair, UserProfile } from "../api/types";
import { AuthContext, type SessionState } from "./context";

const REFRESH_TOKEN_KEY = "dawai.refreshToken";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SessionState>("booting");
  const [user, setUser] = useState<UserProfile | null>(null);
  const accessTokenRef = useRef<string | null>(null);
  const refreshPromiseRef = useRef<Promise<string> | null>(null);

  const clearSession = useCallback(() => {
    accessTokenRef.current = null;
    refreshPromiseRef.current = null;
    window.sessionStorage.removeItem(REFRESH_TOKEN_KEY);
    setUser(null);
    setState("anonymous");
  }, []);

  const commitTokens = useCallback((tokens: TokenPair) => {
    accessTokenRef.current = tokens.access_token;
    window.sessionStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
  }, []);

  const refreshAccessToken = useCallback(async () => {
    const storedRefreshToken = window.sessionStorage.getItem(REFRESH_TOKEN_KEY);
    if (!storedRefreshToken) {
      throw new ApiError(401, "No refresh token is available");
    }
    if (!refreshPromiseRef.current) {
      refreshPromiseRef.current = requestJson<TokenPair>("/api/auth/refresh", {
        method: "POST",
        body: JSON.stringify({ refresh_token: storedRefreshToken }),
      })
        .then((tokens) => {
          commitTokens(tokens);
          return tokens.access_token;
        })
        .finally(() => {
          refreshPromiseRef.current = null;
        });
    }
    return refreshPromiseRef.current;
  }, [commitTokens]);

  const request = useCallback(
    async <T,>(path: string, init: RequestInit = {}) => {
      try {
        return await requestJson<T>(path, init, accessTokenRef.current ?? undefined);
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 401) {
          throw error;
        }
      }

      try {
        const refreshedAccessToken = await refreshAccessToken();
        return await requestJson<T>(path, init, refreshedAccessToken);
      } catch (error) {
        clearSession();
        throw error;
      }
    },
    [clearSession, refreshAccessToken],
  );

  const login = useCallback(
    async (email: string, password: string) => {
      const formData = new URLSearchParams({ username: email.trim(), password });
      const tokens = await requestJson<TokenPair>("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: formData,
      });
      commitTokens(tokens);
      try {
        const profile = await requestJson<UserProfile>(
          "/api/account/me",
          {},
          tokens.access_token,
        );
        setUser(profile);
        setState("authenticated");
      } catch (error) {
        clearSession();
        throw error;
      }
    },
    [clearSession, commitTokens],
  );

  const logout = useCallback(async () => {
    const accessToken = accessTokenRef.current;
    clearSession();
    if (!accessToken) return;
    try {
      await requestJson<{ detail: string }>(
        "/api/auth/logout",
        { method: "POST" },
        accessToken,
      );
    } catch {
      return;
    }
  }, [clearSession]);

  useEffect(() => {
    let active = true;
    const bootstrap = async () => {
      if (!window.sessionStorage.getItem(REFRESH_TOKEN_KEY)) {
        if (active) setState("anonymous");
        return;
      }
      try {
        const accessToken = await refreshAccessToken();
        const profile = await requestJson<UserProfile>("/api/account/me", {}, accessToken);
        if (active) {
          setUser(profile);
          setState("authenticated");
        }
      } catch {
        if (active) clearSession();
      }
    };
    void bootstrap();
    return () => {
      active = false;
    };
  }, [clearSession, refreshAccessToken]);

  const value = useMemo(
    () => ({ state, user, login, logout, request }),
    [state, user, login, logout, request],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
