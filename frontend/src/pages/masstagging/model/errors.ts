// The refusal code of a 409 envelope, or `null` for anything else.
export function conflictCode(error: unknown): string | null {
  if (typeof error !== 'object' || error === null) return null;
  const detail = (error as { error?: { code?: string; message?: string } }).error;
  return detail?.code === 'conflict' ? (detail.message ?? null) : null;
}
