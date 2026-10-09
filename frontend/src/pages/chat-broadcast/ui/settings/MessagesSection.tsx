// The chain: cards open one at a time; a text (optionally with a photo, the text becoming
// its caption) or a post of the operator's channel forwarded whole, each sent one or more
// times a round. Above it, who writes the first message — the template or the AI for each
// chat — and the rewrite switch; below it, one round as a chat will see it.
import { useMutation } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { uploadChatBroadcastPhotoMutation } from '@/entities/chat-broadcast';
import {
  Badge,
  Button,
  HelpHint,
  Icon,
  IconButton,
  Input,
  SegmentedControl,
  Spinner,
  Switch,
  Textarea,
} from '@/shared/ui';

import type { Draft, MessageDraft, Settings } from '../../model/draft';
import { emptyMessage, MAX_MESSAGES, MAX_REPEAT, VARIANTS } from '../../model/draft';
import { filledMessages, roundSteps } from '../../model/preview';

import { Eyebrow, Row } from './fields';

function TextFate({ text, randomize }: { text: string; randomize: boolean }) {
  const { t } = useTranslation();
  if (randomize) {
    return (
      <div className="flex items-center gap-2 rounded-lg bg-info-tint px-3 py-2 text-small text-info-strong">
        <Icon name="sparkles" size={14} className="shrink-0" />
        {t('chatBroadcast.settings.messages.fateAi')}
      </div>
    );
  }
  if (text.trim() === '') return null;
  if (VARIANTS.test(text)) {
    return (
      <div className="rounded-lg bg-canvas px-3 py-2 text-small text-content-muted">
        {t('chatBroadcast.settings.messages.fateVariants')}
      </div>
    );
  }
  return (
    <div className="flex items-start gap-2 rounded-lg bg-warning-tint px-3 py-2 text-small text-warning-deep">
      <Icon name="alert-triangle" size={14} className="mt-1 shrink-0" />
      <span>{t('chatBroadcast.settings.messages.fateSame')}</span>
    </div>
  );
}

// Will every copy of this message read differently? The AI writes or rewrites its text,
// or the text has variants to pick from; a forwarded post is the same post each time.
// `index` is the message's place among the FILLED ones, as in the engine's chain, and the
// engine's own conditions apply: the AI writes the first message only from a brief, and
// the rewrite only touches a text that is there (a photo alone goes out as it is).
function variesPerCopy(message: MessageDraft, settings: Settings, index: number): boolean {
  if (message.kind === 'post') return false;
  if (index === 0 && settings.first_message === 'ai' && settings.ai_brief.trim() !== '') {
    return true;
  }
  if (settings.first_message === 'template' && settings.randomize && message.text.trim() !== '') {
    return true;
  }
  return VARIANTS.test(message.text);
}

function RepeatStepper({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-2">
      <span className="type-small">{t('chatBroadcast.settings.messages.repeat')}</span>
      <HelpHint
        text={t('chatBroadcast.settings.messages.repeatHint')}
        example={t('chatBroadcast.settings.messages.repeatExample')}
      />
      <span className="inline-flex items-center gap-1">
        <IconButton
          size="sm"
          aria-label={t('chatBroadcast.settings.messages.repeatLess')}
          disabled={value <= 1}
          onClick={() => {
            onChange(value - 1);
          }}
        >
          <span aria-hidden="true">−</span>
        </IconButton>
        <span aria-live="polite" className="px-1 text-center type-body-medium tabular-nums">
          {t('chatBroadcast.settings.messages.times', { count: value })}
        </span>
        <IconButton
          size="sm"
          aria-label={t('chatBroadcast.settings.messages.repeatMore')}
          disabled={value >= MAX_REPEAT}
          onClick={() => {
            onChange(value + 1);
          }}
        >
          <Icon name="plus" size={12} />
        </IconButton>
      </span>
    </div>
  );
}

// One round in one chat, step by step — shown once a message repeats, when the order is
// no longer simply the list above.
function RoundLine({ draft }: { draft: Draft }) {
  const { t } = useTranslation();
  const steps = roundSteps(draft);
  // Only what the engine will send: an empty message is dropped, repeats and all.
  const filled = filledMessages(draft);
  if (!filled.some((message) => message.repeat > 1)) return null;
  const same = filled.some(
    (message, index) => message.repeat > 1 && !variesPerCopy(message, draft.settings, index),
  );
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1 rounded-lg bg-canvas px-3 py-2">
        <span className="mr-1 type-small-medium">{t('chatBroadcast.settings.messages.round')}</span>
        {steps.map((step, index) => (
          <span key={`${String(step.number)}-${String(step.copy)}`} className="contents">
            {index === 0 ? null : (
              <Icon name="arrow-right" size={10} className="text-content-subtle" />
            )}
            <span
              className={`rounded-full border bg-surface-card px-2 text-small tabular-nums ${step.copy === 0 ? 'border-line' : 'border-dashed border-line-strong text-content-muted'}`}
            >
              {step.copy === 0
                ? step.number
                : t('chatBroadcast.settings.messages.roundCopy', {
                    number: step.number,
                    copy: step.copy + 1,
                  })}
            </span>
          </span>
        ))}
        <span className="ml-auto type-small">
          {t('chatBroadcast.settings.messages.roundTotal', { count: steps.length })}
        </span>
      </div>
      {same ? (
        <div className="flex items-start gap-2 rounded-lg bg-warning-tint px-3 py-2 text-small text-warning-deep">
          <Icon name="alert-triangle" size={14} className="mt-1 shrink-0" />
          <span>{t('chatBroadcast.settings.messages.repeatSame')}</span>
        </div>
      ) : null}
    </div>
  );
}

function PhotoPicker({
  message,
  onChange,
}: {
  message: MessageDraft;
  onChange: (next: MessageDraft) => void;
}) {
  const { t } = useTranslation();
  const fileRef = useRef<HTMLInputElement>(null);
  const upload = useMutation(uploadChatBroadcastPhotoMutation());
  const pick = () => fileRef.current?.click();
  return (
    <>
      <input
        ref={fileRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file === undefined) return;
          void upload
            .mutateAsync({ body: { file } })
            .then((stored) => {
              onChange({
                ...message,
                photo: {
                  mediaId: stored.media_id,
                  name: stored.name,
                  url: URL.createObjectURL(file),
                },
              });
            })
            .catch(() => undefined);
        }}
      />
      {upload.isPending ? (
        <div className="flex items-center gap-2 type-small">
          <Spinner />
          {t('chatBroadcast.settings.messages.uploading')}
        </div>
      ) : message.photo === null ? (
        <div>
          <Button size="sm" variant="ghost" onClick={pick}>
            <Icon name="paperclip" size={14} />
            {t('chatBroadcast.settings.messages.attach')}
          </Button>
        </div>
      ) : (
        <div className="flex items-center gap-3 rounded-lg border border-line bg-canvas p-2">
          {message.photo.url === null ? (
            <span className="flex size-face shrink-0 items-center justify-center rounded-md bg-info-tint text-info-strong">
              <Icon name="paperclip" size={18} />
            </span>
          ) : (
            <img
              src={message.photo.url}
              alt=""
              className="size-face shrink-0 rounded-md object-cover"
            />
          )}
          <span className="min-w-0 flex-1">
            <span className="block truncate text-body">{message.photo.name}</span>
            <span className="block type-small">{t('chatBroadcast.settings.messages.caption')}</span>
          </span>
          <Button size="sm" variant="ghost" onClick={pick}>
            {t('chatBroadcast.settings.messages.replace')}
          </Button>
          <IconButton
            size="sm"
            shape="circle"
            aria-label={t('chatBroadcast.settings.messages.removePhoto')}
            onClick={() => {
              onChange({ ...message, photo: null });
            }}
          >
            <Icon name="close" size={14} />
          </IconButton>
        </div>
      )}
    </>
  );
}

function MessageCard({
  index,
  message,
  open,
  settings,
  onToggle,
  onChange,
  onDelete,
}: {
  index: number;
  message: MessageDraft;
  open: boolean;
  settings: Settings;
  onToggle: () => void;
  onChange: (next: MessageDraft) => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  const randomize = settings.first_message === 'template' && settings.randomize;
  const rewritten = randomize && message.kind === 'text';
  let kind = t('chatBroadcast.settings.messages.kindText');
  if (message.kind === 'post') kind = t('chatBroadcast.settings.messages.kindPost');
  else if (message.photo !== null) kind = t('chatBroadcast.settings.messages.kindPhoto');
  const preview = (message.kind === 'post' ? message.post : message.text).trim();
  return (
    <div
      className={`rounded-lg border bg-surface-card ${open ? 'border-action-primary' : 'border-line'}`}
    >
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <span
            className={`flex size-glyph shrink-0 items-center justify-center rounded-full text-small font-medium ${open ? 'bg-action-primary text-on-fill' : 'bg-canvas text-content-muted'}`}
          >
            {index + 1}
          </span>
          <span className="shrink-0 type-body-medium">{kind}</span>
          {message.photo === null || message.kind === 'post' ? null : (
            <Icon name="paperclip" size={14} className="shrink-0 text-content-subtle" />
          )}
          {rewritten ? (
            <span
              title={t('chatBroadcast.settings.messages.aiRewrites')}
              className="inline-flex shrink-0"
            >
              <Icon name="sparkles" size={14} className="text-action-primary" />
            </span>
          ) : null}
          <span className="min-w-0 flex-1 truncate type-small">
            {preview === '' ? t('chatBroadcast.settings.messages.empty') : preview}
          </span>
          {message.repeat > 1 ? (
            <Badge tone="info" size="xs">
              {t('chatBroadcast.settings.messages.times', { count: message.repeat })}
            </Badge>
          ) : null}
          {index === 0 ? null : (
            <span className="hidden shrink-0 type-small sm:inline">
              {t('chatBroadcast.settings.messages.after', settings.between_messages)}
            </span>
          )}
          <Icon
            name="chevron-down"
            size={14}
            className={`shrink-0 text-content-subtle transition ${open ? 'rotate-180' : ''}`}
          />
        </button>
        <IconButton
          size="sm"
          tone="danger"
          aria-label={t('chatBroadcast.settings.messages.delete')}
          onClick={onDelete}
        >
          <Icon name="trash" size={14} />
        </IconButton>
      </div>
      {open ? (
        <div className="flex flex-col gap-2 border-t border-canvas px-3 py-3">
          <SegmentedControl
            variant="pill"
            value={message.kind}
            ariaLabel={t('chatBroadcast.settings.messages.kind')}
            options={[
              { value: 'text', label: t('chatBroadcast.settings.messages.kindText') },
              { value: 'post', label: t('chatBroadcast.settings.messages.kindPost') },
            ]}
            onChange={(next) => {
              onChange({ ...message, kind: next });
            }}
          />
          {message.kind === 'post' ? (
            <>
              <Input
                size="sm"
                value={message.post}
                placeholder={t('chatBroadcast.settings.messages.postPlaceholder')}
                aria-label={t('chatBroadcast.settings.messages.postLabel')}
                onChange={(event) => {
                  onChange({ ...message, post: event.target.value });
                }}
              />
              <span className="type-small">{t('chatBroadcast.settings.messages.postNote')}</span>
            </>
          ) : (
            <>
              <Textarea
                size="sm"
                value={message.text}
                placeholder={t('chatBroadcast.settings.messages.textPlaceholder')}
                aria-label={t('chatBroadcast.settings.messages.textLabel')}
                onChange={(event) => {
                  onChange({ ...message, text: event.target.value });
                }}
              />
              <TextFate text={message.text} randomize={randomize} />
              <PhotoPicker message={message} onChange={onChange} />
            </>
          )}
          <RepeatStepper
            value={message.repeat}
            onChange={(repeat) => {
              onChange({ ...message, repeat });
            }}
          />
        </div>
      ) : null}
    </div>
  );
}

export function MessagesSection({
  draft,
  onPatch,
  onMessages,
}: {
  draft: Draft;
  onPatch: (next: Partial<Settings>) => void;
  onMessages: (next: MessageDraft[]) => void;
}) {
  const { t } = useTranslation();
  const { settings, messages } = draft;
  const [openId, setOpenId] = useState<number | null>(messages[0]?.id ?? null);
  return (
    <section>
      <Eyebrow
        title={t('chatBroadcast.settings.messages.title')}
        caption={t('chatBroadcast.settings.messages.count', { count: messages.length })}
        hint={
          <HelpHint
            text={t('chatBroadcast.settings.messages.hint')}
            example={t('chatBroadcast.settings.messages.example')}
          />
        }
      />
      <Row
        first
        label={t('chatBroadcast.settings.messages.first')}
        hint={t('chatBroadcast.settings.messages.firstHint')}
        example={t('chatBroadcast.settings.messages.firstExample')}
      >
        <SegmentedControl
          variant="pill"
          value={settings.first_message}
          ariaLabel={t('chatBroadcast.settings.messages.first')}
          options={[
            { value: 'template', label: t('chatBroadcast.settings.messages.template') },
            { value: 'ai', label: t('chatBroadcast.settings.messages.ai') },
          ]}
          onChange={(value) => {
            onPatch({ first_message: value });
          }}
        />
      </Row>
      {settings.first_message === 'template' ? (
        <Row
          label={t('chatBroadcast.settings.messages.randomize')}
          hint={t('chatBroadcast.settings.messages.randomizeHint')}
          example={t('chatBroadcast.settings.messages.randomizeExample')}
        >
          <Switch
            checked={settings.randomize}
            onChange={(value) => {
              onPatch({ randomize: value });
            }}
            label={t('chatBroadcast.settings.messages.randomize')}
          />
        </Row>
      ) : (
        <div className="pb-2">
          <Textarea
            size="sm"
            value={settings.ai_brief}
            placeholder={t('chatBroadcast.settings.messages.briefPlaceholder')}
            aria-label={t('chatBroadcast.settings.messages.brief')}
            onChange={(event) => {
              onPatch({ ai_brief: event.target.value });
            }}
          />
        </div>
      )}
      <div className="flex flex-col gap-2 pt-2">
        {messages.map((message, index) => (
          <MessageCard
            key={message.id}
            index={index}
            message={message}
            open={openId === message.id}
            settings={settings}
            onToggle={() => {
              setOpenId((current) => (current === message.id ? null : message.id));
            }}
            onChange={(next) => {
              onMessages(messages.map((item) => (item.id === next.id ? next : item)));
            }}
            onDelete={() => {
              onMessages(messages.filter((item) => item.id !== message.id));
            }}
          />
        ))}
        {messages.length === 0 ? (
          <div className="rounded-lg border border-dashed border-line-strong px-3 py-4 text-center type-small">
            {t('chatBroadcast.settings.messages.none')}
          </div>
        ) : null}
        <RoundLine draft={draft} />
        <Button
          variant="dashed"
          fullWidth
          className="font-medium"
          disabled={messages.length >= MAX_MESSAGES}
          onClick={() => {
            const next = emptyMessage(messages);
            onMessages([...messages, next]);
            setOpenId(next.id);
          }}
        >
          {t('chatBroadcast.settings.messages.add')}
        </Button>
      </div>
    </section>
  );
}
