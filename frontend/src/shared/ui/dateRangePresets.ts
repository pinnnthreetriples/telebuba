// The presets row under `DateRangePicker`. A module of its own rather than a constant
// beside the component, so the component file exports components only (fast refresh).
import { addDays, type DateRange } from '@/shared/lib/dateRange';

export interface DateRangePreset {
  id: string;
  label: string;
  /** The range this preset picks, counted from `anchor` — the committed check-in, the
   *  pending one, or today. */
  getRange: (anchor: Date) => DateRange;
}

/** «Выходные» snaps to the next Friday (or the anchor itself on a Friday); the rest count
 *  forward from the anchor. */
export const DEFAULT_DATE_RANGE_PRESETS: DateRangePreset[] = [
  {
    id: 'weekend',
    label: 'Выходные',
    getRange: (anchor) => {
      const friday = addDays(anchor, (5 - anchor.getDay() + 7) % 7);
      return { start: friday, end: addDays(friday, 2) };
    },
  },
  { id: '3-nights', label: '3 ночи', getRange: (a) => ({ start: a, end: addDays(a, 3) }) },
  { id: '1-week', label: '1 неделя', getRange: (a) => ({ start: a, end: addDays(a, 7) }) },
  { id: '2-weeks', label: '2 недели', getRange: (a) => ({ start: a, end: addDays(a, 14) }) },
];
