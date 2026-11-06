const SCHEME = /^([a-z][a-z0-9+.-]*):/i;
const ALLOWED = new Set(['http:', 'https:', 'tg:']);

// A scheme-less address gets `https://`; http, https and tg pass; anything else
// (javascript:, data:, ftp:) is refused. Returns the normalised URL or null.
export function normalizeUrl(raw: string): string | null {
  const text = raw.trim();
  if (text === '' || /\s/.test(text)) return null;
  const scheme = SCHEME.exec(text);
  // `example.com:8080/x` looks like a scheme to the regexp; a host:port has digits after
  // the colon, a scheme has `//` or a tg resource.
  const isHostPort = scheme !== null && /^[^:/]+:\d+(?:[/?#]|$)/.test(text);
  const candidate = scheme === null || isHostPort ? `https://${text}` : text;
  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    return null;
  }
  if (!ALLOWED.has(parsed.protocol)) return null;
  if (
    parsed.protocol !== 'tg:' &&
    !parsed.hostname.includes('.') &&
    parsed.hostname !== 'localhost'
  ) {
    return null;
  }
  if (parsed.protocol === 'tg:' && parsed.href === 'tg:') return null;
  return candidate;
}

export function isValidUrl(raw: string): boolean {
  return normalizeUrl(raw) !== null;
}
