import { createContext } from "react";

import type { UserProfile } from "../api/types";

export type SessionState = "booting" | "anonymous" | "authenticated";

export interface AuthContextValue {
  state: SessionState;
  user: UserProfile | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  request: <T>(path: string, init?: RequestInit) => Promise<T>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
