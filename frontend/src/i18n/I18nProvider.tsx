import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { I18nContext } from "./context";
import { messages, type Locale, type MessageKey } from "./messages";

const LOCALE_STORAGE_KEY = "dawai.locale";

function getInitialLocale(): Locale {
  const stored = window.localStorage.getItem(LOCALE_STORAGE_KEY);
  return stored === "en" ? "en" : "ar";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(getInitialLocale);

  const setLocale = useCallback((nextLocale: Locale) => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, nextLocale);
    setLocaleState(nextLocale);
  }, []);

  const toggleLocale = useCallback(() => {
    setLocale(locale === "ar" ? "en" : "ar");
  }, [locale, setLocale]);

  useEffect(() => {
    const direction = locale === "ar" ? "rtl" : "ltr";
    document.documentElement.lang = locale;
    document.documentElement.dir = direction;
    document.title = locale === "ar" ? "DawAI | تشغيل الصيدلية" : "DawAI | Pharmacy workspace";
  }, [locale]);

  const t = useCallback(
    (key: MessageKey, variables: Record<string, string | number> = {}) => {
      let translated: string = messages[locale][key];
      for (const [name, value] of Object.entries(variables)) {
        translated = translated.replaceAll(`{{${name}}}`, String(value));
      }
      return translated;
    },
    [locale],
  );

  const value = useMemo(
    () => ({
      locale,
      direction: (locale === "ar" ? "rtl" : "ltr") as "rtl" | "ltr",
      setLocale,
      toggleLocale,
      t,
    }),
    [locale, setLocale, t, toggleLocale],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}
