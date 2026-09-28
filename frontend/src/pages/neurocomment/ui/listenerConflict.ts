export function isWarmingConflict(error: unknown): boolean {
  if (typeof error !== 'object' || error === null) return false;
  const detail = (error as { error?: { code?: string; message?: string } }).error;
  return detail?.code === 'conflict' && detail.message === 'listener_busy_warming';
}
