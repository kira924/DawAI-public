import { createContext } from "react";

import type { Locale, MessageKey } from "./messages";

export interface I18nContextValue {
  locale: Locale;
  direction: "rtl" | "ltr";
  setLocale: (locale: Locale) => void;
  toggleLocale: () => void;
  t: (key: MessageKey, variables?: Record<string, string | number>) => string;
}

export const I18nContext = createContext<I18nContextValue | null>(null);
