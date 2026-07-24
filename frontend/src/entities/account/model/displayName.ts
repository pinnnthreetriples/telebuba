// The Telegram display name (first + last), falling back to the phone/id for
// accounts not yet checked — so every surface (accounts table, warming cards,
// neurocomment modal) shows one primary label the same way.
export function accountDisplayName(a: {
  first_name?: string | null;
  last_name?: string | null;
  phone?: string | null;
  account_id: string;
}): string {
  return [a.first_name, a.last_name].filter(Boolean).join(' ') || a.phone || a.account_id;
}

// Mono-avatar fallback initials: name initials when the Telegram name is known
// (first + last initial), else the last two phone/id digits (matching the
// design). Shared so every avatar (accounts table, warming cards) falls back the
// same way.
export function accountInitials(a: {
  first_name?: string | null;
  last_name?: string | null;
  phone?: string | null;
  account_id: string;
}): string {
  const name = [a.first_name, a.last_name].filter(Boolean).join(' ').trim();
  if (name) {
    // Spread to code points so an emoji / non-BMP initial isn't split into a
    // lone surrogate half.
    const parts = name.split(/\s+/);
    const first = [...(parts[0] ?? '')][0] ?? '';
    const second = [...(parts[1] ?? '')][0] ?? '';
    return (first + second).toUpperCase();
  }
  const digits = (a.phone ?? a.account_id).replace(/\D/g, '');
  return digits.slice(-2) || '#';
}
