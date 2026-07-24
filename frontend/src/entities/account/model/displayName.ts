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
