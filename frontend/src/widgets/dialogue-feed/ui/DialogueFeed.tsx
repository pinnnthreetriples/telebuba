import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Badge, Card, Icon } from '@/shared/ui';

import { warmingDialoguesQueryOptions } from '@/entities/warming';
import type { DialogueFeedMessage } from '@/shared/api';
import { FOCUS_RING } from '@/shared/design-system';
import { formatLocalTime } from '@/shared/lib';

import { daysAgo, type DialoguePair, groupIntoPairs, isFresh, messageKey } from '../model/pairs';

// Newest-first from the API; each pair's transcript reads like a live chat
// (oldest at the top, newest at the bottom). This poll is short so new lines
// appear + animate in.
const FEED_LIMIT = 30;
const FEED_POLL_MS = 4000;

// How long ago, said the way it reads: today the clock time, yesterday the word,
// older the number of days. A bare «14:03» on an exchange from last week is the
// same lie the pulsing dot used to tell.
function Age({ iso }: { iso: string }) {
  const { t } = useTranslation();
  const days = daysAgo(iso);
  if (days === null || days <= 0) return formatLocalTime(iso);
  if (days === 1) return t('warming.dialogues.yesterday');
  return t('warming.dialogues.daysAgo', { n: days });
}

// Three staggered dots on the shared dotspin keyframe — a subtle "typing…" pulse
// that signals the accounts are still chatting. Spans, not divs: it also sits
// inside the pair row's `<button>`, where a block element would be invalid.
function TypingIndicator() {
  const { t } = useTranslation();
  return (
    <span className="flex items-center gap-sm type-caption">
      <span className="flex items-center gap-xs">
        {[0, 1, 2].map((index) => (
          <span
            key={index}
            className="tb-typing-dot size-tick rounded-full bg-action-primary"
            style={{ animationDelay: `${String(index * 0.16)}s` }}
          />
        ))}
      </span>
      {t('warming.dialogues.typing')}
    </span>
  );
}

// One pair's replies. Pure view — takes the already-loaded messages so it is
// trivial to test with fed data and to reason about the enter animation in
// isolation.
//
// The side says who wrote it, and that is what made the card fit a 340px
// column: the «А → Б» line over EVERY reply wrapped to three lines there, while
// the pair is already named in the row above.
export function DialogueTranscript({
  messages,
  leftAccount,
}: {
  messages: DialogueFeedMessage[];
  leftAccount: string;
}) {
  const seenKeys = useRef<Set<string>>(new Set());
  const endRef = useRef<HTMLDivElement>(null);

  // A message animates only the first time we render its key; later polls that
  // still contain it must stay put (no re-animation on every 4s tick).
  //
  // Read during render, WRITTEN in an effect — the same rule as the pair rows in
  // `DialogueFeed`, and for the same reason: under `StrictMode` (see `main.tsx`)
  // development renders twice, so a key marked seen while rendering put out its
  // own signal — the second pass already found it seen, and that is the pass on
  // screen. An effect runs once per commit, so dev and production animate alike.
  const entering = new Set(messages.map(messageKey).filter((key) => !seenKeys.current.has(key)));

  useEffect(() => {
    for (const message of messages) seenKeys.current.add(messageKey(message));
  }, [messages]);

  useEffect(() => {
    // jsdom has no scrollIntoView; guard so tests (and any host without it) pass.
    //
    // Доводит до вида список, а НЕ страницу, и это проверено замером, а не
    // выведено из спецификации: раскрытие последней пары из семи оставляет
    // `window.scrollY` на месте (в браузере до щелчка 470, после 470). Первый
    // замер говорил обратное — там страницу уводил сам Playwright, который
    // доводит элемент до вида перед щелчком.
    endRef.current?.scrollIntoView?.({ block: 'end' });
  }, [messages]);

  const oldest = messages[0];
  if (!oldest) return null;

  return (
    <div className="flex flex-col gap-tight border-t border-line-row pt-md">
      {/* The oldest line ON THIS PAGE — not the exchange's start: the feed is a
          sliding window of the last 30 messages app-wide, which is the same
          reason `pairs.ts` refuses to derive the sides from it. The only
          timestamp in the transcript: at 308px of content width a per-reply
          time costs a line each. */}
      <span className="self-center tabular-nums type-caption">
        <Age iso={oldest.created_at} />
      </span>
      {messages.map((message) => {
        const key = messageKey(message);
        const left = message.from_account === leftAccount;
        return (
          <div
            key={key}
            // `break-words` is the fix for the reason this widget could not move
            // into the narrow column: a joinchat link is one unbreakable token
            // and used to hang out of the bubble.
            className={`max-w-[84%] break-words rounded-lg px-md py-sm text-body text-content-secondary ${
              left
                ? 'self-start rounded-tl-[3px] border border-line bg-surface-card'
                : 'self-end rounded-tr-[3px] bg-info-tint'
            } ${entering.has(key) ? 'tb-swapin' : ''}`}
          >
            {message.text}
          </div>
        );
      })}
      {isFresh(messages.at(-1)?.created_at ?? '') ? <TypingIndicator /> : null}
      <div ref={endRef} />
    </div>
  );
}

// A pair, closed: who with whom, how many replies, how long ago — or the typing
// pulse instead of the last line while the two are mid-exchange. The whole row
// is the control (`FOCUS_RING` and the app's `.tb-row` hover), because a chevron
// alone is a 16px target for a 308px row.
function PairRow({
  pair,
  open,
  onToggle,
  entering,
}: {
  pair: DialoguePair;
  open: boolean;
  onToggle: () => void;
  entering: boolean;
}) {
  const live = isFresh(pair.newestAt);
  return (
    <div
      // Вплывает только ЗАКРЫТАЯ строка: у раскрытой новая реплика заставляла
      // подпрыгивать всю панель вместе с перепиской, хотя вплыть должен один
      // пузырь внутри — он это и делает сам.
      //
      // `shrink-0` — не косметика: список пар это flex-колонка с потолком, а
      // `overflow-hidden` снимает с элемента его АВТОМИНИМУМ (правило действует
      // только при `overflow: visible`). Без запрета сжатия восемь пар не
      // прокручивали список, а жались в 420px, обрезая себе вторую строку —
      // подпись с последней репликой и сроком пропадала совсем.
      className={`tb-row shrink-0 overflow-hidden rounded-lg border ${
        open ? 'border-line-strong bg-surface' : 'border-line bg-surface-card'
      } ${entering && !open ? 'tb-swapin' : ''}`}
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className={`flex w-full flex-col px-md py-md text-left ${FOCUS_RING}`}
      >
        <span className="flex w-full items-center gap-sm">
          <span className="flex min-w-0 flex-1 items-center gap-tight type-label">
            {/* Both sides truncate. Two untruncated names plus the arrow and the
                time is what wrapped the old header into three lines here. */}
            <span className={`truncate ${open ? 'font-semibold text-content-primary' : ''}`}>
              {pair.leftName}
            </span>
            <Icon name="arrow-right" size={12} className="shrink-0 text-content-subtle" />
            {/* Blue for the right-hand side, the same blue its bubbles carry —
                that pairing is the legend for which side is whose. */}
            <span className={`truncate ${open ? 'font-semibold text-info-strong' : ''}`}>
              {pair.rightName}
            </span>
          </span>
          <Badge tone={live ? 'success' : 'neutral'} className={live ? 'font-bold' : undefined}>
            {pair.messages.length}
          </Badge>
          <Icon
            name="chevron-down"
            size={16}
            className={`shrink-0 text-content-subtle transition-transform duration-reveal ease-spring ${
              open ? 'rotate-180' : ''
            }`}
          />
        </span>
        {open ? null : (
          <span className="mt-hair flex w-full items-center gap-tight type-caption">
            {live ? (
              <TypingIndicator />
            ) : (
              <>
                <span className="truncate">{pair.messages.at(-1)?.text}</span>
                <span className="ml-auto shrink-0 tabular-nums">
                  <Age iso={pair.newestAt} />
                </span>
              </>
            )}
          </span>
        )}
      </button>
      {open ? (
        <div className="px-md pb-md">
          <DialogueTranscript messages={pair.messages} leftAccount={pair.leftAccount} />
        </div>
      ) : null}
    </div>
  );
}

// The design's card language: white rounded card, a title with a live-dot (the
// «Система активна» pattern — pulsing green only while the feed is actually
// fresh, static and muted otherwise) and a count. Polls the dialogue feed so new
// inter-account messages appear and animate in live.
//
// A LIST OF PAIRS rather than one flat transcript, because the card now lives in
// the page's 340px column: one exchange is named once instead of over every
// reply, and the operator opens the one they want to read.
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
    // re-renders and `isFresh` re-reads `Date.now()` — the dot goes quiet
    // within one poll of the threshold instead of staying green forever after
    // the last exchange. Cheaper than a second timer, and it reuses the poll we
    // already pay for rather than shortening it.
    notifyOnChangeProps: 'all',
  });
  // Keyed on `data`, not on the derived array: with `notifyOnChangeProps: 'all'`
  // every poll re-renders, and a fresh `messages` array each time would make the
  // transcript's scroll-to-end effect fire every four seconds.
  const pairs = useMemo(() => groupIntoPairs(data?.messages ?? []), [data]);
  // One pair open at a time: several open transcripts stretch the column past
  // the board beside it.
  const [openKey, setOpenKey] = useState<string | null>(null);
  // Живой приход, который список пар иначе потерял: пузыри уехали внутрь пары,
  // и на свёрнутой карточке анимировать стало нечего — до этого каждое новое
  // сообщение вплывало прямо на странице. Теперь вплывает СТРОКА: на первой
  // отрисовке и каждый раз, когда у пары появляется новая реплика (заодно она
  // уезжает наверх, потому что список отсортирован по свежести).
  //
  // Признак — метка последней реплики, а не флаг: опрос раз в четыре секунды
  // возвращает ту же строку, и по метке «то же самое» отличается от «новое»
  // без второго запроса и без состояния, которое надо чистить.
  //
  // Запись — в ЭФФЕКТЕ, а рендер её только читает, и это не стиль: под
  // `StrictMode` (см. `main.tsx`) в разработке рендер выполняется дважды, и
  // отметка, поставленная во время рендера, гасила бы собственный признак —
  // второй проход уже видел строку «виденной», а на экран попадает именно он.
  // Эффект выполняется один раз на коммит, поэтому dev и прод ведут себя
  // одинаково.
  const seenAt = useRef<Map<string, string>>(new Map());
  const entering = new Set(
    pairs.filter((pair) => seenAt.current.get(pair.key) !== pair.newestAt).map((pair) => pair.key),
  );
  useEffect(() => {
    for (const pair of pairs) seenAt.current.set(pair.key, pair.newestAt);
  }, [pairs]);
  // The freshest pair sorts first, so the card's own dot is that pair's — read
  // from the clock on every render, not from the memoised page.
  const live = isFresh(pairs[0]?.newestAt ?? '');

  return (
    <Card className="p-lg">
      <div className="mb-lg flex items-center gap-md">
        {/* Pulsing green only while the feed is genuinely fresh; otherwise the
            static muted dot the design already uses for an idle listener. */}
        <span
          className={`size-dot shrink-0 rounded-full ${live ? 'tb-livedot bg-success' : 'bg-content-subtle'}`}
        />
        <span className="min-w-0 flex-1 type-card-title">{t('warming.dialogues.title')}</span>
        {pairs.length > 0 ? (
          <Badge tone={live ? 'success' : 'neutral'} className={live ? 'font-bold' : undefined}>
            {t('warming.dialogues.pairs', { count: pairs.length })}
          </Badge>
        ) : null}
      </div>
      {pairs.length === 0 ? (
        <div className="py-page text-center type-prose">{t('warming.dialogues.empty')}</div>
      ) : (
        // The list is the ONE scroll: an open transcript grows inside it rather
        // than scrolling on its own, because two nested scrollbars in a 340px
        // column are unusable. Taller than `max-h-feed` deliberately — the pairs
        // moved here to spend the column's height.
        // eslint-disable-next-line design-tokens/no-raw-values -- see the note in the rule: this card's own list height, one component's internal layout
        <div className="tb-scroll flex max-h-[420px] flex-col gap-sm overflow-y-auto pr-xs">
          {pairs.map((pair) => (
            <PairRow
              key={pair.key}
              pair={pair}
              entering={entering.has(pair.key)}
              open={openKey === pair.key}
              onToggle={() => {
                setOpenKey(openKey === pair.key ? null : pair.key);
              }}
            />
          ))}
        </div>
      )}
    </Card>
  );
}
