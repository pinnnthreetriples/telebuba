import type { RunSchedule } from '@/shared/api';

export type FullSchedule = Required<RunSchedule>;

// The server's defaults, for the moment before the saved limits load.
export const DEFAULT_SCHEDULE: FullSchedule = {
  work_start: '09:00',
  work_end: '20:00',
  work_days: [0, 1, 2, 3, 4, 5],
  operator_timezone: 'Europe/Moscow',
  client_timezone: true,
  series_size: 10,
  series_break_min_seconds: 900,
  series_break_max_seconds: 1800,
  concurrency: 2,
};

export function fullSchedule(schedule: RunSchedule | undefined): FullSchedule {
  return { ...DEFAULT_SCHEDULE, ...schedule };
}
