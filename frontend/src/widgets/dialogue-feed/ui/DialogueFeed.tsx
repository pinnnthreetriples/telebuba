import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';

import { warmingDialoguesQueryOptions } from '@/entities/warming';
import type { DialogueFeedMessage } from '@/shared/api';
import { formatLocalTime } from '@/shared/lib';

// Newest-first from the API; the feed reads like a live chat (oldest at the top,
// newest at the bottom). This poll is short so new lines appear + animate in.
const FEED_LIMIT = 30;
const FEED_POLL_MS = 4000;

// No id on the wire — a message is uniquely the two accounts + its timestamp.
function messageKey(message: DialogueFeedMessage): string {
  return `${message.from_account}→${message.to_account}@${message.created_at}`;
}

function DialogueRow({ message, isNew }: { message: DialogueFeedMessage; isNew: boolean }) {
  return (
    <div className={isNew ? 'tb-swapin' : undefined}>
      <div className="mb-[3px] flex items-center gap-[5px] text-[10.5px] text-ink-subtle">
        <span className="font-medium text-ink-muted">{message.from_label}</span>
        <svg
          width="11"
          height="11"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
        >
          <path d="M5 12h14M13 6l6 6-6 6" />
        </svg>
        <span className="font-medium text-ink-muted">{message.to_label}</span>
        <span className="ml-auto shrink-0 tabular-nums text-[10px] text-line-strong">
          {formatLocalTime(message.created_at)}
        </span>
      </div>
      <div className="inline-block max-w-full rounded-[10px] rounded-tl-[3px] bg-[#f7f6f4] px-[11px] py-[7px] text-[12px] leading-[1.45] text-[#3a3a3a]">
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
    <div className="mt-[2px] flex items-center gap-[6px] text-[10.5px] text-ink-subtle">
      <span className="flex items-center gap-[3px]">
        {[0, 1, 2].map((index) => (
          <span
            key={index}
            className="tb-typing-dot h-[4px] w-[4px] rounded-full bg-primary"
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
      <div className="py-[34px] text-center text-[12px] text-ink-subtle">
        {t('warming.dialogues.empty')}
      </div>
    );
  }

  return (
    <div className="tb-scroll flex max-h-[260px] flex-col gap-[10px] overflow-y-auto pr-1">
      {ordered.map((message) => {
        const key = messageKey(message);
        return <DialogueRow key={key} message={message} isNew={isNew(key)} />;
      })}
      <TypingIndicator />
      <div ref={endRef} />
    </div>
  );
}

// The design's card language: white rounded card, a title with a pulsing green
// live-dot (the «Система активна» pattern) and a count. Polls the dialogue feed
// so new inter-account messages appear and animate in live.
export function DialogueFeed() {
  const { t } = useTranslation();
  const { data } = useQuery({
    ...warmingDialoguesQueryOptions({ query: { limit: FEED_LIMIT } }),
    refetchInterval: FEED_POLL_MS,
  });
  const messages = data?.messages ?? [];

  return (
    <div className="mt-4 rounded-2xl border border-line bg-white p-4">
      <div className="mb-[13px] flex items-center gap-[9px]">
        <span className="tb-livedot h-[7px] w-[7px] shrink-0 rounded-full bg-success" />
        <span className="text-[14px] font-bold">{t('warming.dialogues.title')}</span>
        {messages.length > 0 ? (
          <span className="rounded-full bg-success-tint px-2 py-[2px] text-[10.5px] font-bold text-success">
            {messages.length}
          </span>
        ) : null}
      </div>
      <DialogueTranscript messages={messages} />
    </div>
  );
}
