// A category («Категория») is a saved list of chat links that is COPIED into a campaign:
// nothing links the two afterwards. The server collapses duplicates by chat key on save;
// this only keeps the visible list from showing the same link twice.
import type { ChatBroadcastResolvedTarget } from '@/shared/api';

// The same chat spelled differently: `@Chat`, `t.me/chat`, `https://t.me/chat/`.
// Usernames ignore case; invite hashes and folder slugs do not.
export function linkKey(raw: string): string {
  const key = raw
    .trim()
    .replace(/^https?:\/\//i, '')
    .replace(/^(www\.)?(t|telegram)\.me\//i, '')
    .replace(/^@/, '')
    .replace(/\/+$/, '');
  return guessKind(raw) === 'public' ? key.toLowerCase() : key;
}

// `current` first, in its order, then the links of `added` it does not hold yet.
export function mergeTargets(current: string[], added: string[]): string[] {
  const seen = new Set(current.map(linkKey));
  const merged = [...current];
  for (const raw of added) {
    const key = linkKey(raw);
    if (key === '' || seen.has(key)) continue;
    seen.add(key);
    merged.push(raw);
  }
  return merged;
}

export function sameTargets(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((raw, index) => raw === right[index]);
}

// What a link is before the server has looked at it.
export function guessKind(raw: string): ChatBroadcastResolvedTarget['kind'] {
  if (/addlist\//i.test(raw)) return 'folder';
  if (raw.includes('+') || /joinchat\//i.test(raw)) return 'invite';
  return 'public';
}
