// "Check how the broadcast will go": opens on Save and plays the scenario step by step on
// one account, with a clock. Blocking warnings disable Confirm; soft ones only inform.
// Confirm is the save.
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AccountAvatar, accountDisplayName } from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { Badge, Button, Icon, IconButton, Modal, Notice, ProgressBar } from '@/shared/ui';

import type { Draft } from '../../model/draft';
import type { Block, Step, StepTone } from '../../model/preview';
import { buildBlocks, filledMessages, roundsOf, upToOf, warningsOf } from '../../model/preview';

const STEP_MS = 900;

const DOT: Record<StepTone, string> = {
  join: 'bg-info-tint text-info-strong',
  wait: 'bg-warning-tint text-warning-deep',
  msg: 'bg-action-primary text-on-fill',
  done: 'bg-success-tint text-success-deep',
};

function Bubble({
  bubble,
  author,
  time,
  randomized,
}: {
  bubble: NonNullable<Step['bubble']>;
  author: string;
  time: string;
  randomized: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="tb-swapin mt-2 max-w-name">
      <div className="overflow-hidden rounded-lg rounded-bl-sm border border-line bg-surface-card shadow-thumb">
        {bubble.photo ? (
          <div className="flex h-tile items-center justify-center gap-2 bg-info-tint text-small text-info-strong">
            <Icon name="paperclip" size={14} />
            {t('chatBroadcast.preview.photo')}
          </div>
        ) : null}
        <div className="px-3 py-2">
          <div className="type-body-medium text-action-primary">{author}</div>
          {bubble.post ? (
            <div className="mb-1 text-small text-info-strong">
              {t('chatBroadcast.preview.forwarded')}
            </div>
          ) : null}
          <div className="text-body">{bubble.text || '—'}</div>
          <div className="mt-1 text-right type-small tabular-nums">{time}</div>
        </div>
      </div>
      {randomized && !bubble.post ? (
        <div className="mt-1 flex items-center gap-1 type-small">
          <Icon name="sparkles" size={12} className="text-action-primary" />
          {t('chatBroadcast.preview.aiRewrites')}
        </div>
      ) : null}
    </div>
  );
}

function BlockView({
  block,
  reached,
  finished,
  author,
  randomized,
}: {
  block: Extract<Block, { kind: 'chat' }>;
  reached: number;
  finished: boolean;
  author: string;
  randomized: boolean;
}) {
  return (
    <div className="tb-fadeup overflow-hidden rounded-lg border border-line bg-surface-card">
      <div className="flex items-center gap-3 border-b border-canvas bg-surface px-3 py-2">
        <span className="flex size-icon shrink-0 items-center justify-center rounded-full bg-info-tint text-info-strong">
          <Icon name={block.icon} size={14} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate type-body-medium">{block.title}</span>
          <span className="block type-small">{block.caption}</span>
        </span>
      </div>
      <ol className="m-0 list-none px-3 py-2">
        {block.steps.map((step, at) => {
          let state: 'done' | 'now' | 'next' = 'next';
          if (at < reached || (at === reached && finished)) state = 'done';
          else if (at === reached) state = 'now';
          return (
            <li
              key={`${step.label}-${String(at)}`}
              className={`flex gap-3 transition-opacity duration-reveal ${state === 'next' ? 'opacity-40' : ''}`}
            >
              <div className="flex flex-col items-center">
                <span
                  className={`mt-1 flex size-chip shrink-0 items-center justify-center rounded-full ${state === 'next' ? 'bg-canvas text-content-subtle' : DOT[step.tone]} ${state === 'now' ? 'ring-2 ring-action-primary ring-offset-2' : ''}`}
                >
                  <Icon name={step.icon} size={12} />
                </span>
                {at === block.steps.length - 1 ? null : (
                  <span className="flex-1 border-l border-line" />
                )}
              </div>
              <div className="min-w-0 flex-1 pb-3">
                <div className="flex items-baseline gap-2">
                  <span className={`text-body ${state === 'now' ? 'font-medium' : ''}`}>
                    {step.label}
                  </span>
                  <span className="ml-auto shrink-0 type-small tabular-nums">{step.time}</span>
                </div>
                {step.note === undefined ? null : <div className="type-small">{step.note}</div>}
                {step.bubble !== undefined && state !== 'next' ? (
                  <Bubble
                    bubble={step.bubble}
                    author={author}
                    time={step.time}
                    randomized={randomized}
                  />
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

export function Preview({
  draft,
  accounts,
  ownTitles,
  saving,
  onEdit,
  onConfirm,
}: {
  draft: Draft;
  accounts: AccountRead[];
  ownTitles: string[];
  saving: boolean;
  onEdit: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  const blocks = buildBlocks(t, draft, ownTitles);
  const starts: number[] = [];
  let total = 0;
  for (const block of blocks) {
    starts.push(total);
    total += block.kind === 'chat' ? block.steps.length : 1;
  }
  const reduced =
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const [tick, setTick] = useState(reduced ? total : 0);
  const [playing, setPlaying] = useState(!reduced);
  const finished = tick >= total;

  useEffect(() => {
    if (!playing || finished) return;
    const timer = window.setTimeout(() => {
      setTick((value) => value + 1);
    }, STEP_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [playing, finished, tick]);

  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!playing) return;
    const box = scrollRef.current;
    box?.scrollTo?.({ top: box.scrollHeight, behavior: reduced ? 'auto' : 'smooth' });
  }, [tick, playing, reduced]);

  const lead = accounts[0];
  const author = lead === undefined ? t('chatBroadcast.preview.account') : accountDisplayName(lead);
  const { blocking, soft } = warningsOf(t, draft);
  const randomized = draft.settings.first_message === 'template' && draft.settings.randomize;
  const own = draft.settings.target_mode === 'own';
  const rounds = roundsOf(draft);
  const upTo = upToOf(draft);

  let now = t('chatBroadcast.preview.shown');
  if (!finished) {
    blocks.forEach((block, index) => {
      const start = starts[index] ?? 0;
      if (block.kind === 'divider' && start === tick) now = block.label;
      if (block.kind === 'chat' && tick >= start && tick < start + block.steps.length) {
        const step = block.steps[tick - start];
        if (step !== undefined) now = `${block.title}: ${step.label.toLowerCase()}`;
      }
    });
  }
  let playLabel = t('chatBroadcast.preview.resume');
  if (finished) playLabel = t('chatBroadcast.preview.replay');
  else if (playing) playLabel = t('chatBroadcast.preview.pausePlay');
  let playIcon: 'refresh' | 'pause' | 'play' = 'play';
  if (finished) playIcon = 'refresh';
  else if (playing) playIcon = 'pause';

  return (
    <Modal onClose={onEdit} size="panel" label={t('chatBroadcast.preview.title')}>
      <div className="flex max-h-dialog flex-col overflow-hidden">
        <div className="border-b border-canvas px-6 py-6">
          <h2 className="type-h2">{t('chatBroadcast.preview.title')}</h2>
          <div className="mt-1 type-body text-content-subtle">
            {t('chatBroadcast.preview.subtitle')}
          </div>
          <div className="mt-4 rounded-lg bg-canvas px-3 py-3">
            <div className="flex items-center gap-3">
              <span className="flex shrink-0 items-center">
                {accounts.map((row, index) => (
                  <span
                    key={row.account_id}
                    title={row.username ? `@${row.username}` : accountDisplayName(row)}
                    className={`rounded-full border-2 border-canvas ${index === 0 ? '' : '-ml-2'}`}
                  >
                    <AccountAvatar
                      account={row}
                      className="size-icon rounded-full"
                      fallbackClassName="bg-info-tint text-small font-medium text-info-strong"
                    />
                  </span>
                ))}
              </span>
              <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1 text-body">
                <span className="font-medium">
                  {t('chatBroadcast.preview.accounts', { count: draft.accountIds.length })}
                </span>
                <Icon name="arrow-right" size={12} className="text-content-subtle" />
                <span className="font-medium">
                  {own
                    ? t('chatBroadcast.preview.own')
                    : t('chatBroadcast.preview.chats', { count: draft.settings.targets.length })}
                </span>
                <Icon name="arrow-right" size={12} className="text-content-subtle" />
                <span className="font-medium">
                  {t('chatBroadcast.preview.messagesRounds', {
                    messages: filledMessages(draft).length,
                    rounds:
                      rounds === null
                        ? t('chatBroadcast.preview.endless')
                        : t('chatBroadcast.preview.rounds', { count: rounds }),
                  })}
                </span>
              </div>
            </div>
            {upTo === null && !randomized ? null : (
              <div className="mt-2 flex flex-wrap gap-2">
                {upTo === null ? null : (
                  <Badge tone="info">{t('chatBroadcast.preview.upTo', { count: upTo })}</Badge>
                )}
                {randomized ? (
                  <Badge tone="info">
                    <Icon name="sparkles" size={12} />
                    {t('chatBroadcast.preview.ai')}
                  </Badge>
                ) : null}
              </div>
            )}
          </div>
        </div>

        <div ref={scrollRef} className="tb-scroll flex-1 overflow-y-auto px-6 py-4">
          <div className="flex flex-col gap-3">
            {blocks.map((block, index) => {
              const start = starts[index] ?? 0;
              if (tick < start) return null;
              if (block.kind === 'divider') {
                return (
                  <div key={block.id} className="tb-fadeup flex items-center gap-3">
                    <span className="flex-1 border-t border-line" />
                    <span
                      className={`flex items-center gap-1 rounded-full px-3 py-1 text-small font-medium ${block.strong ? 'bg-success-tint text-success-deep' : 'bg-canvas text-content-muted'}`}
                    >
                      <Icon name={block.strong ? 'check-circle' : 'clock'} size={12} />
                      {block.label}
                    </span>
                    <span className="flex-1 border-t border-line" />
                  </div>
                );
              }
              return (
                <BlockView
                  key={block.id}
                  block={block}
                  reached={tick - start}
                  finished={finished}
                  author={author}
                  randomized={randomized}
                />
              );
            })}
          </div>
        </div>

        <div className="flex flex-col gap-2 border-t border-canvas px-6 py-4">
          {blocking.map((text) => (
            <Notice key={text} tone="danger" bordered={false}>
              {text}
            </Notice>
          ))}
          {soft.map((text) => (
            <Notice key={text} tone="warning" bordered={false}>
              {text}
            </Notice>
          ))}
          <div className="flex items-center gap-3">
            <IconButton
              size="md"
              shape="circle"
              aria-label={playLabel}
              onClick={() => {
                if (finished) {
                  setTick(0);
                  setPlaying(true);
                } else {
                  setPlaying((value) => !value);
                }
              }}
            >
              <Icon name={playIcon} size={14} />
            </IconButton>
            <div className="min-w-0 flex-1">
              <div className="mb-1 truncate type-small">{now}</div>
              <ProgressBar value={Math.min(tick, total)} max={total} className="w-full" />
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button onClick={onEdit}>{t('chatBroadcast.preview.edit')}</Button>
            <Button variant="primary" disabled={blocking.length > 0 || saving} onClick={onConfirm}>
              {saving ? t('chatBroadcast.preview.saving') : t('chatBroadcast.preview.confirm')}
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
