import type { SimEvent } from "@/lib/worldModel";
import type { Dictionary } from "./types";
import { interpolate } from "./translate";

/** Resolve ISO3 codes in common entity params to localized country names. */
function localizeEntityParams(
  params: Record<string, string | number>,
  dict: Dictionary,
): Record<string, string | number> {
  const out = { ...params };
  for (const key of ["country", "proxy", "hub", "source", "target"]) {
    const val = out[key];
    if (typeof val === "string" && /^[A-Z]{3}$/.test(val)) {
      out[key] = localizedCountryName(val, dict, val);
    }
  }
  return out;
}

/** Resolve a stored sim event into the active locale. */
export function formatSimEvent(event: SimEvent, dict: Dictionary): string {
  const params = localizeEntityParams(
    { ...event.messageParams },
    dict,
  );

  if (params.labelKey) {
    const key = String(params.labelKey);
    params.label = dict.macro.features[key] ?? key;
    delete params.labelKey;
  }
  if (params.dirKey === "rose" || params.dirKey === "eased") {
    params.dir = dict.simDirs[params.dirKey as "rose" | "eased"];
    delete params.dirKey;
  }
  if (params.actionKey === "raised" || params.actionKey === "cut") {
    params.action = dict.simTariff[params.actionKey as "raised" | "cut"];
    delete params.actionKey;
  }
  if (params.exportActionKey === "fall" || params.exportActionKey === "recover") {
    params.exportAction =
      dict.simTariff[params.exportActionKey as "fall" | "recover"];
    delete params.exportActionKey;
  }

  const template = dict.simEvents[event.messageKey];
  if (!template) return event.messageKey;
  return interpolate(template, params);
}

export function formatSimSource(source: string, dict: Dictionary): string {
  return dict.simSources[source] ?? source;
}

export function localizedCountryName(
  code: string,
  dict: Dictionary,
  fallback?: string,
): string {
  return dict.countries[code] ?? fallback ?? code;
}

export function localizedContinent(
  name: string,
  dict: Dictionary,
): string {
  return dict.continents[name] ?? name;
}
