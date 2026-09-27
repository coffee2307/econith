/** Build an absolute browser WebSocket URL without baking a host into the app. */
export function browserWebSocketUrl(
  path: string,
  configured?: string,
): string {
  const explicit = configured?.trim();
  if (explicit) return explicit;
  if (typeof window === "undefined") return "";

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${protocol}//${window.location.host}${normalizedPath}`;
}
