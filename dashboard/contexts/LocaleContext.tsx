"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { en } from "@/locales/en";
import { vi } from "@/locales/vi";
import {
  formatSimEvent,
  formatSimSource,
  localizedContinent,
  localizedCountryName,
} from "@/lib/i18n/formatSimEvent";
import { makeTranslator, type TranslateFn } from "@/lib/i18n/translate";
import type { Dictionary, Locale } from "@/lib/i18n/types";
import type { SimEvent } from "@/lib/worldModel";
import { syncLocale } from "@/lib/api";

const STORAGE_KEY = "econith-locale";

const DICTS: Record<Locale, Dictionary> = { en, vi };

function applyLocale(locale: Locale) {
  const root = document.documentElement;
  root.lang = locale === "vi" ? "vi" : "en";
  root.dataset.locale = locale;
}

interface LocaleContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: TranslateFn;
  dict: Dictionary;
  featureLabel: (key: string) => string;
  countryName: (code: string, fallback?: string) => string;
  continentName: (name: string) => string;
  simEventMessage: (event: SimEvent) => string;
  simEventSource: (source: string) => string;
}

const LocaleContext = createContext<LocaleContextValue | null>(null);

export function LocaleProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>("en");

  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "en" || stored === "vi") {
      setLocaleState(stored);
      applyLocale(stored);
      return;
    }
    const browser =
      typeof navigator !== "undefined" &&
      navigator.language.toLowerCase().startsWith("vi")
        ? "vi"
        : "en";
    setLocaleState(browser);
    applyLocale(browser);
  }, []);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    applyLocale(next);
    localStorage.setItem(STORAGE_KEY, next);
    void syncLocale(next);
  }, []);

  useEffect(() => {
    void syncLocale(locale);
  }, [locale]);

  const dict = DICTS[locale];

  const t = useMemo(() => makeTranslator(dict), [dict]);

  const featureLabel = useCallback(
    (key: string) => dict.macro.features[key] ?? key,
    [dict],
  );

  const countryName = useCallback(
    (code: string, fallback?: string) =>
      localizedCountryName(code, dict, fallback),
    [dict],
  );

  const continentName = useCallback(
    (name: string) => localizedContinent(name, dict),
    [dict],
  );

  const simEventMessage = useCallback(
    (event: SimEvent) => formatSimEvent(event, dict),
    [dict],
  );

  const simEventSource = useCallback(
    (source: string) => formatSimSource(source, dict),
    [dict],
  );

  const value = useMemo(
    () => ({
      locale,
      setLocale,
      t,
      dict,
      featureLabel,
      countryName,
      continentName,
      simEventMessage,
      simEventSource,
    }),
    [
      locale,
      setLocale,
      t,
      dict,
      featureLabel,
      countryName,
      continentName,
      simEventMessage,
      simEventSource,
    ],
  );

  return (
    <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>
  );
}

export function useLocale() {
  const ctx = useContext(LocaleContext);
  if (!ctx) {
    throw new Error("useLocale must be used within LocaleProvider");
  }
  return ctx;
}

/** Shorthand for translation only. */
export function useT() {
  return useLocale().t;
}
