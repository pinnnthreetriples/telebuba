// A pasted list of chats, sorted before anything is added: what is new, what repeats,
// what is not a chat link at all. Format only — whether the chat exists is the run's
// business, not a paste box's (asking Telegram about hundreds of links costs FloodWait).
//
// The rules mirror the server's `core.channel_tokens.normalize_channel` and
// `services.chat_broadcast.links.parse_target`, so a link that passes here is one the
// server keeps. The key is the chat's identity: `@Chat`, `t.me/chat` and
// `https://t.me/chat/` are one chat; an invite hash and a folder slug keep their case.

const LINK_PREFIXES = ['https://t.me/', 'http://t.me/', 't.me/', 'telegram.me/'];
const INVITE_RE = /^[A-Za-z0-9_-]{8,64}$/;
const SLUG_RE = /^[A-Za-z0-9_-]{1,64}$/;
const USERNAME_RE = /^[A-Za-z0-9_]{3,32}$/;
// Separators between links: whitespace (a column pasted from a table), commas, semicolons.
const SEPARATOR_RE = /[\s,;]+/;
// Wrapping a link picks up in prose and lists: quotes, brackets, a full stop.
const WRAPPING_RE = /^["'«»“”(<[]+|["'«»“”)>\].:!?]+$/g;
// List markers that carry no link: `1.`, `2)`, `-`, `•`, `—`. Skipped, not reported.
const MARKER_RE = /^(\d+[.)]?|[-–—•*·])$/;

export type ChatLinkKind = 'public' | 'invite' | 'folder';

function stripLink(value: string): { rest: string; prefixed: boolean } {
  const cleaned = value.replace(/\/+$/, '').split('?', 1)[0] ?? '';
  const lowered = cleaned.toLowerCase();
  for (const prefix of LINK_PREFIXES) {
    if (lowered.startsWith(prefix)) return { rest: cleaned.slice(prefix.length), prefixed: true };
  }
  return { rest: cleaned, prefixed: false };
}

/** The chat a link names, as `{ key, kind }`, or `null` when it is not a chat link. */
export function chatLinkKey(
  raw: string,
  { allowFolders }: { allowFolders: boolean },
): { key: string; kind: ChatLinkKind } | null {
  const { rest, prefixed } = stripLink(raw.trim());
  if (prefixed && /^addlist(\/|$)/i.test(rest)) {
    const slug = rest.slice('addlist/'.length);
    return allowFolders && SLUG_RE.test(slug) ? { key: `addlist:${slug}`, kind: 'folder' } : null;
  }
  const invite = rest.startsWith('+')
    ? rest.slice(1)
    : rest.toLowerCase().startsWith('joinchat/')
      ? rest.slice('joinchat/'.length)
      : null;
  if (invite !== null) return INVITE_RE.test(invite) ? { key: `+${invite}`, kind: 'invite' } : null;
  // A bare `channel/123` is not a link; `t.me/channel/123` is a post in that channel.
  if (!prefixed && rest.includes('/')) return null;
  const name = rest.replace(/^@+/, '');
  if (name.toLowerCase().startsWith('c/')) return null;
  const username = name.split('/', 1)[0] ?? '';
  return USERNAME_RE.test(username) ? { key: username.toLowerCase(), kind: 'public' } : null;
}

export type PasteAnalysis = {
  /** Links to add, once each, in the order pasted. */
  fresh: string[];
  /** Links the list already holds. */
  existing: string[];
  /** Second and later copies of a link within the paste itself. */
  repeated: string[];
  /** Pieces that are not a chat link. */
  invalid: string[];
};

export function analyzePaste(
  text: string,
  current: readonly string[],
  { allowFolders }: { allowFolders: boolean },
): PasteAnalysis {
  const known = new Set<string>();
  for (const raw of current) {
    const parsed = chatLinkKey(raw, { allowFolders: true });
    if (parsed) known.add(parsed.key);
  }
  const seen = new Set<string>();
  const result: PasteAnalysis = { fresh: [], existing: [], repeated: [], invalid: [] };
  for (const piece of text.split(SEPARATOR_RE)) {
    const token = piece.replace(WRAPPING_RE, '');
    if (token === '' || MARKER_RE.test(token)) continue;
    const parsed = chatLinkKey(token, { allowFolders });
    if (parsed === null) result.invalid.push(token);
    else if (known.has(parsed.key)) result.existing.push(token);
    else if (seen.has(parsed.key)) result.repeated.push(token);
    else {
      seen.add(parsed.key);
      result.fresh.push(token);
    }
  }
  return result;
}
