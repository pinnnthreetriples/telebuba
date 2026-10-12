import type { StartPreviewItem } from '@/shared/api';

// One time zone's slice of a start preview: the first and last moment any of its
// accounts can wake, already written in that zone's clock.
export type StartPreviewZone = {
  timeZone: string;
  from: string;
  to: string;
  shifted: boolean;
};

function clock(iso: string, timeZone: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit', timeZone }).format(
    new Date(iso),
  );
}

// The backend previews each account on its own; the dialog speaks per time zone, so
// a pool of one country reads as a single range and a mixed pool as one per zone.
export function groupStartPreview(items: StartPreviewItem[], locale: string): StartPreviewZone[] {
  const zones = new Map<string, { first: string; last: string; shifted: boolean }>();
  for (const item of items) {
    const zone = zones.get(item.time_zone);
    if (!zone) {
      zones.set(item.time_zone, {
        first: item.earliest_at,
        last: item.latest_at,
        shifted: item.shifted,
      });
      continue;
    }
    if (Date.parse(item.earliest_at) < Date.parse(zone.first)) zone.first = item.earliest_at;
    if (Date.parse(item.latest_at) > Date.parse(zone.last)) zone.last = item.latest_at;
    zone.shifted ||= item.shifted;
  }
  return [...zones].map(([timeZone, zone]) => ({
    timeZone,
    from: clock(zone.first, timeZone, locale),
    to: clock(zone.last, timeZone, locale),
    shifted: zone.shifted,
  }));
}
