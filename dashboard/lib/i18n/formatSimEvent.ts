import type { SimEvent } from "@/lib/worldModel";
import type { Dictionary } from "./types";
import { interpolate } from "./translate";

/** Resolve a stored sim event into the active locale. */
export function formatSimEvent(event: SimEvent, dict: Dictionary): string {
  const params: Record<string, string | number> = { ...event.messageParams };

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
