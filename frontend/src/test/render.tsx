import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import type { UserProfile } from "../api/types";
import { AuthContext } from "../auth/context";
import type { AuthContextValue } from "../auth/context";
import { I18nProvider } from "../i18n/I18nProvider";

export const managerProfile: UserProfile = {
  id: 4,
  email: "manager@example.test",
  full_name: "Test Manager",
  role: "manager",
  tenant_id: 2,
  created_at: "2026-09-05T00:00:00Z",
};

export function renderWithProviders(
  element: ReactElement,
  overrides: Partial<AuthContextValue> = {},
) {
  const defaultRequest: AuthContextValue["request"] = async () => {
    throw new Error("Unexpected API request in test");
  };
  const auth: AuthContextValue = {
    state: "authenticated",
    user: managerProfile,
    login: async () => undefined,
    logout: async () => undefined,
    request: defaultRequest,
    ...overrides,
  };

  return render(
    <I18nProvider>
      <AuthContext.Provider value={auth}>{element}</AuthContext.Provider>
    </I18nProvider>,
  );
}
