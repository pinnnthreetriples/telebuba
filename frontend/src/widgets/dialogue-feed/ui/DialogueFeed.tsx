import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';

import { Badge, Card, Icon } from '@/shared/ui';

import { accountDisplayName } from '@/entities/account';
import { warmingDialoguesQueryOptions } from '@/entities/warming';
import type { DialogueFeedMessage } from '@/shared/api';
import { formatLocalTime } from '@/shared/lib';

// Newest-first from the API; the feed reads like a live chat (oldest at the top,
// newest at the bottom). This poll is short so new lines appear + animate in.
const FEED_LIMIT = 30;
const FEED_POLL_MS = 4000;

// How recent the newest message must be for the card to claim the accounts are
// chatting *now*. Warming cycles are hours apart, so this is deliberately
// narrow: it covers the gap between two lines of one exchange (a reply lands
// within seconds), not the gap between cycles. Two minutes ≈ 30 poll ticks —
// wide enough to survive a slow reply, and short enough that an idle feed goes
// quiet almost immediately. The honest consequence is that the live dot and
// «печатает…» are off most of the time; that is the point — before this, a feed
// whose newest line was five days old still advertised itself as live.
const FEED_LIVE_MS = 120_000;

// No id on the wire — a message is uniquely the two accounts + its timestamp.
function messageKey(message: DialogueFeedMessage): string {
  return `${message.from_account}→${message.to_account}@${message.created_at}`;
}

// Liveness from data already on hand: the newest message's age. No extra field
// and no extra request — the 4s poll refreshes `messages`, and each render
// re-evaluates the age against the current clock.
//
// `created_at` is an ISO-8601 stamp carrying an explicit UTC offset (the backend
// writes `datetime.now(UTC).isoformat()`), so `Date.parse` — the same parse
// `formatLocalTime` does via `new Date(iso)` — resolves it to an absolute
// instant. Subtracting two absolute instants is timezone-free; only the
// *rendering* in `formatLocalTime` is local. An unparseable stamp reads as not
// live rather than as `NaN < threshold` noise.
function isFeedLive(messages: DialogueFeedMessage[]): boolean {
  // The API is newest-first (same ordering `DialogueTranscript` reverses), so
  // the head is the freshest line.
  const newest = messages[0];
  if (!newest) return false;
  const createdAt = Date.parse(newest.created_at);
  return !Number.isNaN(createdAt) && Date.now() - createdAt < FEED_LIVE_MS;
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

function DialogueRow({ message, isNew }: { message: DialogueFeedMessage; isNew: boolean }) {
  return (
    <div className={isNew ? 'tb-swapin' : undefined}>
      <div className="mb-xs flex items-center gap-tight type-meta">
        <span className="font-medium text-ink-muted">
          {participant(message.from_label, message.from_first_name, message.from_last_name)}
        </span>
        <Icon name="arrow-right" size={12} />
        <span className="font-medium text-ink-muted">
          {participant(message.to_label, message.to_first_name, message.to_last_name)}
        </span>
        <span className="ml-auto shrink-0 tabular-nums type-meta">
          {formatLocalTime(message.created_at)}
        </span>
      </div>
      <div className="inline-block max-w-full rounded-lg rounded-tl-[3px] bg-surface px-md py-sm text-body leading-[1.45] text-ink-body">
        {message.text}
      </div>
    </div>
  );
}

// Three staggered dots on the shared dotspin keyframe — a subtle "typing…" pulse
// that signals the accounts are still chatting.
function TypingIndicator() {
  const { t } = useTranslation();
  return (
    <div className="mt-hair flex items-center gap-sm type-meta">
      <span className="flex items-center gap-xs">
        {[0, 1, 2].map((index) => (
          <span
            key={index}
            className="tb-typing-dot size-tick rounded-full bg-primary"
            style={{ animationDelay: `${String(index * 0.16)}s` }}
          />
        ))}
      </span>
      {t('warming.dialogues.typing')}
    </div>
  );
}

// Pure transcript view — takes the already-loaded messages so it is trivial to
// test with fed data and to reason about the enter animation in isolation.
export function DialogueTranscript({ messages }: { messages: DialogueFeedMessage[] }) {
  const { t } = useTranslation();
  const seenKeys = useRef<Set<string>>(new Set());
  const endRef = useRef<HTMLDivElement>(null);

  // Oldest → newest for chat reading order (the API is newest-first).
  const ordered = [...messages].reverse();

  // A message animates only the first time we render its key; later polls that
  // still contain it must stay put (no re-animation on every 4s tick).
  const isNew = (key: string): boolean => {
    if (seenKeys.current.has(key)) return false;
    seenKeys.current.add(key);
    return true;
  };

  useEffect(() => {
    // jsdom has no scrollIntoView; guard so tests (and any host without it) pass.
    endRef.current?.scrollIntoView?.({ block: 'end' });
  }, [messages]);

  if (ordered.length === 0) {
    return (
      <div className="py-page text-center text-body text-ink-subtle">
        {t('warming.dialogues.empty')}
      </div>
    );
  }

  return (
    <div className="tb-scroll flex max-h-feed flex-col gap-md overflow-y-auto pr-xs">
      {ordered.map((message) => {
        const key = messageKey(message);
        return <DialogueRow key={key} message={message} isNew={isNew(key)} />;
      })}
      {isFeedLive(messages) ? <TypingIndicator /> : null}
      <div ref={endRef} />
    </div>
  );
}

// The design's card language: white rounded card, a title with a live-dot (the
// «Система активна» pattern — pulsing green only while the feed is actually
// fresh, static and muted otherwise) and a count. Polls the dialogue feed so new
// inter-account messages appear and animate in live.
export function DialogueFeed() {
  const { t } = useTranslation();
  const { data } = useQuery({
    ...warmingDialoguesQueryOptions({ query: { limit: FEED_LIMIT } }),
    refetchInterval: FEED_POLL_MS,
    // Liveness is a function of the clock, so it has to decay without new data.
    // By default this component would not re-render on an idle feed at all: the
    // poll returns an identical payload, structural sharing hands back the same
    // `data` reference, and the tracked-props optimisation only notifies on
    // properties actually read. `'all'` opts out of that, so every poll settling
    // re-renders and `isFeedLive` re-reads `Date.now()` — the dot goes quiet
    // within one poll of the threshold instead of staying green forever after
    // the last exchange. Cheaper than a second timer, and it reuses the poll we
    // already pay for rather than shortening it.
    notifyOnChangeProps: 'all',
  });
  const messages = data?.messages ?? [];
  const live = isFeedLive(messages);

  return (
    <Card className="mt-lg p-lg">
      <div className="mb-lg flex items-center gap-md">
        {/* Pulsing green only while the feed is genuinely fresh; otherwise the
            static muted dot the design already uses for an idle listener. */}
        <span
          className={`size-dot shrink-0 rounded-full ${live ? 'tb-livedot bg-success' : 'bg-ink-subtle'}`}
        />
        <span className="type-card-title">{t('warming.dialogues.title')}</span>
        {messages.length > 0 ? (
          <Badge tone="success" className="font-bold">
            {/* One page, not a total: at the limit there is more history behind
                it, so say "30+" instead of freezing at a wrong-looking "30". */}
            {messages.length === FEED_LIMIT ? `${FEED_LIMIT}+` : messages.length}
          </Badge>
        ) : null}
      </div>
      <DialogueTranscript messages={messages} />
    </Card>
  );
}
