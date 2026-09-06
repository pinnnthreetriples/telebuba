// Pure reading of the dialogue feed — no React, no server calls. Lives in a .ts
// module because react-refresh/only-export-components forbids non-component
// exports from .tsx (see features/channel-discovery/model/progress.ts).
import { accountDisplayName } from '@/entities/account';
import type { DialogueFeedMessage } from '@/shared/api';

// How recent a message must be for the card (or one pair) to claim the accounts
// are chatting *now*. Warming cycles are hours apart, so this is deliberately
// narrow: it covers the gap between two lines of one exchange (a reply lands
// within seconds), not the gap between cycles. Two minutes ≈ 30 poll ticks —
// wide enough to survive a slow reply, and short enough that an idle feed goes
// quiet almost immediately. The honest consequence is that the live dot and
// «печатает…» are off most of the time; that is the point — before this, a feed
// whose newest line was five days old still advertised itself as live.
const FEED_LIVE_MS = 120_000;

// No id on the wire — a message is uniquely the two accounts + its timestamp.
export function messageKey(message: DialogueFeedMessage): string {
  return `${message.from_account}→${message.to_account}@${message.created_at}`;
}

// Liveness from data already on hand: the newest message's age. No extra field
// and no extra request — the 4s poll refreshes the rows, and each render
// re-evaluates the age against the current clock.
//
// `created_at` is an ISO-8601 stamp carrying an explicit UTC offset (the backend
// writes `datetime.now(UTC).isoformat()`), so `Date.parse` — the same parse
// `formatLocalTime` does via `new Date(iso)` — resolves it to an absolute
// instant. Subtracting two absolute instants is timezone-free; only the
// *rendering* is local. An unparseable stamp reads as not live rather than as
// `NaN < threshold` noise.
export function isFresh(iso: string): boolean {
  const at = Date.parse(iso);
  return !Number.isNaN(at) && Date.now() - at < FEED_LIVE_MS;
}

// Calendar days, not 24-hour buckets: «вчера» is yesterday's DATE even when only
// three hours have passed. Both ends are local midnights, so a DST night that is
// 23 or 25 hours long still rounds to one day.
function midnightOf(at: Date): number {
  return new Date(at.getFullYear(), at.getMonth(), at.getDate()).getTime();
}

export function daysAgo(iso: string): number | null {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return null;
  return Math.round((midnightOf(new Date()) - midnightOf(at)) / 86_400_000);
}

// The Telegram name, falling back to the label the API already resolved for us
// (phone → account label → bare id). That label goes in as `account_id` because
// it is this surface's last resort and is `min_length=1` on the wire: the shared
// helper's own phone/id fallbacks are already folded into it, so passing it
// twice would just leave one slot dead.
function participant(
  label: string,
  firstName: string | null | undefined,
  lastName: string | null | undefined,
): string {
  return accountDisplayName({ first_name: firstName, last_name: lastName, account_id: label });
}

// The name of ONE side of a message — whichever side is the given account.
function sideName(message: DialogueFeedMessage, account: string): string {
  return message.from_account === account
    ? participant(message.from_label, message.from_first_name, message.from_last_name)
    : participant(message.to_label, message.to_first_name, message.to_last_name);
}

// One exchange between two accounts, which is what the operator actually looks
// for. The key is UNORDERED (`a|b`, sorted): «Анна→Мия» and «Мия→Анна» are one
// conversation, and keying it by direction split a single exchange across two
// rows of the list.
//
// Same shape the backend already calls canonical — `pair_key` in
// core/repositories/dialogues.py is `"|".join(sorted(...))`, and its schema
// documents the pair as `account_a < account_b`. That column is deliberately not
// on the `DialogueFeedMessage` wire, so this is a re-derivation rather than a
// second source of truth: here the key is only a React key and the open-state
// handle, and the sorted head is what makes the left-hand side stable.
export interface DialoguePair {
  key: string;
  // Which of the two writes on the LEFT. The sorted key's head, not the first
  // speaker: the loaded page is the last 30 messages app-wide, so its oldest
  // line in a pair changes as the window slides — and the sides would swap
  // under the operator mid-read.
  leftAccount: string;
  leftName: string;
  rightName: string;
  // Oldest → newest, chat reading order (the API is newest-first).
  messages: DialogueFeedMessage[];
  // The pair's newest line: what it sorts on, and what its freshness is read
  // from. Freshness itself is NOT stored — it is a function of the clock, and
  // the card memoises these on the fetched page, so a stored flag would keep the
  // typing pulse on forever after the last exchange.
  newestAt: string;
}

export function groupIntoPairs(messages: DialogueFeedMessage[]): DialoguePair[] {
  const buckets = new Map<string, DialogueFeedMessage[]>();
  for (const message of messages) {
    const key = [message.from_account, message.to_account].sort().join('|');
    const bucket = buckets.get(key);
    if (bucket) bucket.push(message);
    else buckets.set(key, [message]);
  }

  const pairs: DialoguePair[] = [];
  for (const [key, bucket] of buckets) {
    // The head is the pair's freshest line (the input is newest-first).
    const newest = bucket[0];
    const [leftAccount] = key.split('|');
    if (!newest || leftAccount == null) continue;
    const rightAccount =
      newest.from_account === leftAccount ? newest.to_account : newest.from_account;
    pairs.push({
      key,
      leftAccount,
      leftName: sideName(newest, leftAccount),
      rightName: sideName(newest, rightAccount),
      messages: [...bucket].reverse(),
      newestAt: newest.created_at,
    });
  }
  // Freshest exchange on top. An unparseable stamp sorts as the epoch rather
  // than turning the whole comparison into NaN.
  return pairs.sort((a, b) => (Date.parse(b.newestAt) || 0) - (Date.parse(a.newestAt) || 0));
}
