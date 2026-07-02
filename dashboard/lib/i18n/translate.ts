import type { Dictionary } from "./types";

export function getNested(obj: unknown, path: string): string | undefined {
  const parts = path.split(".");
  let cur: unknown = obj;
  for (const p of parts) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[p];
  }
  return typeof cur === "string" ? cur : undefined;
}

export function interpolate(
  template: string,
  params?: Record<string, string | number>,
): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (_, key: string) =>
    key in params ? String(params[key]) : `{${key}}`,
  );
}

export function makeTranslator(dict: Dictionary) {
  return function t(
    path: string,
    params?: Record<string, string | number>,
  ): string {
    const raw = getNested(dict, path);
    if (!raw) return path;
    return interpolate(raw, params);
  };
}

export type TranslateFn = ReturnType<typeof makeTranslator>;
