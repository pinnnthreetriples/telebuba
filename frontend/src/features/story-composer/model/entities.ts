import type { CaptionValue, StoryEntity, StoryEntityType } from './types';

// Canonical nesting order, outermost first. Also the sort tie-break, so the same text
// always serialises to the same entity list whatever order the user applied marks in.
export const ENTITY_ORDER: readonly StoryEntityType[] = [
  'text_url',
  'bold',
  'italic',
  'underline',
  'strike',
  'spoiler',
  'code',
];

const rank = (type: StoryEntityType) => ENTITY_ORDER.indexOf(type);

function make(type: StoryEntityType, start: number, end: number, url?: string): StoryEntity {
  const entity: StoryEntity = { type, offset: start, length: end - start };
  if (url !== undefined) entity.url = url;
  return entity;
}

// Clamp to the text, drop empty ranges, fuse touching or overlapping ranges of the same
// kind and sort. Idempotent.
export function normalizeEntities(
  entities: readonly StoryEntity[],
  textLength: number,
): StoryEntity[] {
  const sorted = entities
    .map((entity) => ({
      type: entity.type,
      url: entity.url,
      start: Math.max(0, entity.offset),
      end: Math.min(textLength, entity.offset + entity.length),
    }))
    .filter((entity) => entity.end > entity.start)
    .sort((a, b) => a.start - b.start || a.end - b.end);
  const fused: StoryEntity[] = [];
  for (const item of sorted) {
    const previous = [...fused]
      .reverse()
      .find(
        (candidate) => candidate.type === item.type && (candidate.url ?? '') === (item.url ?? ''),
      );
    if (previous && previous.offset + previous.length >= item.start) {
      previous.length = Math.max(previous.offset + previous.length, item.end) - previous.offset;
    } else {
      fused.push(make(item.type, item.start, item.end, item.url));
    }
  }
  return fused.sort(
    (a, b) => a.offset - b.offset || b.length - a.length || rank(a.type) - rank(b.type),
  );
}

// True when [start, end) is wholly inside entities of `type`.
export function isCovered(
  entities: readonly StoryEntity[],
  type: StoryEntityType,
  start: number,
  end: number,
): boolean {
  if (end <= start) return false;
  let cursor = start;
  const ranges = entities
    .filter((entity) => entity.type === type)
    .sort((a, b) => a.offset - b.offset);
  for (const entity of ranges) {
    if (entity.offset > cursor) break;
    cursor = Math.max(cursor, entity.offset + entity.length);
    if (cursor >= end) return true;
  }
  return false;
}

// The link URL covering the whole range, if one does.
export function linkAt(
  entities: readonly StoryEntity[],
  start: number,
  end: number,
): string | null {
  const link = entities.find(
    (entity) =>
      entity.type === 'text_url' &&
      entity.offset <= start &&
      entity.offset + entity.length >= Math.max(end, start + 1),
  );
  return link?.url ?? null;
}

export function removeRange(
  entities: readonly StoryEntity[],
  type: StoryEntityType,
  start: number,
  end: number,
): StoryEntity[] {
  const out: StoryEntity[] = [];
  for (const entity of entities) {
    const a = entity.offset;
    const b = entity.offset + entity.length;
    if (entity.type !== type || b <= start || a >= end) {
      out.push(entity);
      continue;
    }
    if (a < start) out.push(make(entity.type, a, start, entity.url));
    if (b > end) out.push(make(entity.type, end, b, entity.url));
  }
  return out;
}

// Toggle semantics: a range already fully marked is cleared, anything else is marked
// in full. A link is never toggled — see `setLink`.
export function toggleEntity(
  entities: readonly StoryEntity[],
  type: Exclude<StoryEntityType, 'text_url'>,
  start: number,
  end: number,
  textLength: number,
): StoryEntity[] {
  if (end <= start) return [...entities];
  if (isCovered(entities, type, start, end)) {
    return normalizeEntities(removeRange(entities, type, start, end), textLength);
  }
  return normalizeEntities([...entities, make(type, start, end)], textLength);
}

// A range carries one link: setting replaces whatever was there, `null` clears it.
export function setLink(
  entities: readonly StoryEntity[],
  start: number,
  end: number,
  url: string | null,
  textLength: number,
): StoryEntity[] {
  const cleared = removeRange(entities, 'text_url', start, end);
  if (url === null || end <= start) return normalizeEntities(cleared, textLength);
  return normalizeEntities([...cleared, make('text_url', start, end, url)], textLength);
}

// Replace [start, end) of the text and carry the entities along: marks after the edit
// shift, marks across it stretch or shrink, marks swallowed by it vanish. An insertion
// exactly at a mark's edge stays outside the mark.
export function replaceRange(
  value: CaptionValue,
  start: number,
  end: number,
  insert: string,
): CaptionValue {
  const delta = insert.length - (end - start);
  const text = value.text.slice(0, start) + insert + value.text.slice(end);
  const moved: StoryEntity[] = [];
  for (const entity of value.entities) {
    const a = entity.offset;
    const b = entity.offset + entity.length;
    if (b <= start) {
      moved.push(entity);
      continue;
    }
    if (a >= end) {
      moved.push({ ...entity, offset: a + delta });
      continue;
    }
    const newStart = a < start ? a : start + insert.length;
    const newEnd = b > end ? b + delta : start;
    if (newEnd > newStart) moved.push(make(entity.type, newStart, newEnd, entity.url));
  }
  return { text, entities: normalizeEntities(moved, text.length) };
}
