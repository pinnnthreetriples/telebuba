import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from 'react';
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import type { ChatDialog, ChatMedia, ChatMessage } from '@/shared/api';
import {
  accountChatHistoryInfiniteQueryOptions,
  accountChatsInfiniteQueryOptions,
} from '@/entities/account';
import {
  accountChatMediaFileName,
  markAccountChatReadRequest,
  sendAccountChatMessageRequest,
} from '@/entities/account';
import { FOCUS_RING } from '@/shared/design-system';
import { useLogEventStream } from '@/shared/lib';
import { Badge, Button, Card, Icon, IconButton } from '@/shared/ui';

const MAX_ROWS = 6;
type AccountChatsProps = { accountId: string; overview: ReactNode };

function sendErrorKey(error: unknown): string {
  if (typeof error !== 'object' || error === null) return 'sendError';
  const outer = error as Record<string, unknown>;
  const nested =
    typeof outer.error === 'object' && outer.error !== null
      ? (outer.error as Record<string, unknown>)
      : null;
  const code = nested?.code ?? outer.detail ?? outer.code ?? nested?.message;
  if (code === 'chat_media_too_large') return 'mediaTooLarge';
  if (code === 'payload_too_large') return 'requestTooLarge';
  if (code === 'too_many_files') return 'tooManyFiles';
  return 'sendError';
}

function resizeComposer(node: HTMLTextAreaElement): void {
  const style = window.getComputedStyle(node);
  const lineHeight = Number.parseFloat(style.lineHeight) || 24;
  const padding =
    (Number.parseFloat(style.paddingTop) || 0) + (Number.parseFloat(style.paddingBottom) || 0);
  const maxHeight = lineHeight * MAX_ROWS + padding;
  node.style.height = 'auto';
  node.style.height = `${Math.min(node.scrollHeight, maxHeight)}px`;
  node.style.overflowY = node.scrollHeight > maxHeight ? 'auto' : 'hidden';
}

function messageTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, { dateStyle: 'short', timeStyle: 'short' }).format(date);
}

function mediaLabel(t: (key: string) => string, kind: ChatMedia['kind']): string {
  const keys: Record<ChatMedia['kind'], string> = {
    image: 'image',
    video: 'video',
    audio: 'audio',
    voice: 'voice',
    video_note: 'videoNote',
    sticker: 'sticker',
    document: 'file',
    animation: 'animation',
    unknown: 'file',
  };
  return t(`accounts.edit.chats.${keys[kind]}`);
}

function MediaAttachment({ media, index }: { media: ChatMedia; index: number }) {
  const { t } = useTranslation();
  const [previewOpen, setPreviewOpen] = useState(false);
  const name = accountChatMediaFileName(media, index);
  const previewable = [
    'image',
    'video',
    'audio',
    'voice',
    'video_note',
    'sticker',
    'animation',
  ].includes(media.kind);
  const previewButton = previewable ? (
    <Button variant="secondary" size="sm" onClick={() => setPreviewOpen((open) => !open)}>
      {previewOpen ? t('accounts.edit.chats.hidePreview') : t('accounts.edit.chats.preview')}
    </Button>
  ) : null;
  return (
    <div className="mt-sm flex flex-col gap-sm rounded-lg border border-line bg-surface-card p-sm">
      <div className="flex items-center gap-sm">
        <Icon
          name={
            media.kind === 'video' || media.kind === 'video_note'
              ? 'video'
              : media.kind === 'audio' || media.kind === 'voice'
                ? 'chart'
                : 'file'
          }
          size={16}
        />
        <span className="min-w-0 flex-1 truncate type-label">{name}</span>
        <span className="type-caption text-content-muted">{mediaLabel(t, media.kind)}</span>
        {previewButton}
        <a
          href={media.download_url}
          download={name}
          className="type-caption font-medium text-action-primary underline"
        >
          {t('accounts.edit.chats.download')}
        </a>
      </div>
      {previewOpen ? (
        media.kind === 'image' || media.kind === 'sticker' ? (
          <img
            src={media.download_url}
            alt={name}
            className="max-h-72 max-w-full rounded-md object-contain"
          />
        ) : media.kind === 'video' || media.kind === 'video_note' || media.kind === 'animation' ? (
          <video
            src={media.download_url}
            preload="none"
            controls
            className="max-h-72 max-w-full rounded-md"
          />
        ) : (
          <audio src={media.download_url} preload="none" controls className="max-w-full" />
        )
      ) : null}
    </div>
  );
}

function ChatMessageView({ message, you }: { message: ChatMessage; you: string }) {
  return (
    <article className={`flex flex-col ${message.outgoing ? 'items-end' : 'items-start'}`}>
      <div
        className={`max-w-col break-words rounded-lg px-lg py-md type-prose ${message.outgoing ? 'bg-info-tint text-content-primary' : 'border border-line bg-surface-card text-content-secondary'}`}
      >
        {message.text ? <p className="m-0 whitespace-pre-wrap">{message.text}</p> : null}
        {message.media?.map((media, index) => (
          <MediaAttachment key={`${message.message_id}-${index}`} media={media} index={index} />
        ))}
      </div>
      <span className="mt-xs px-xs tabular-nums type-caption text-content-subtle">
        {messageTime(message.date)}
        {message.outgoing ? ` · ${you}` : ''}
      </span>
    </article>
  );
}

function ChatComposer({
  onSend,
  pending,
  disabled,
}: {
  onSend: (text: string, files: File[]) => Promise<void>;
  pending: boolean;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [droppedFiles, setDroppedFiles] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    if (textarea.current) resizeComposer(textarea.current);
  }, [text]);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if ((!text.trim() && files.length === 0) || pending || disabled) return;
    setError(null);
    try {
      await onSend(text.trim(), files);
      setText('');
      setFiles([]);
      setDroppedFiles(0);
    } catch (caught) {
      setError(sendErrorKey(caught));
    }
  };
  return (
    <form onSubmit={submit} className="border-t border-line-row p-lg">
      {files.length ? (
        <div className="mb-sm flex flex-wrap gap-xs">
          {files.map((file, i) => (
            <span
              key={`${file.name}-${i}`}
              className="inline-flex max-w-full items-center gap-xs rounded-md bg-canvas px-sm py-xs type-caption"
            >
              <span className="truncate">{file.name}</span>
              <IconButton
                size="sm"
                shape="circle"
                aria-label={t('accounts.edit.chats.removeFile', { name: file.name })}
                onClick={() => setFiles((list) => list.filter((_, n) => n !== i))}
              >
                <Icon name="close" size={12} />
              </IconButton>
            </span>
          ))}
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="mb-sm type-caption text-danger">
          {t(`accounts.edit.chats.${error}`)}
        </p>
      ) : null}
      <div className="flex flex-col gap-sm sm:flex-row sm:items-end">
        <textarea
          ref={textarea}
          rows={1}
          value={text}
          disabled={disabled || pending}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              e.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder={t('accounts.edit.chats.messagePlaceholder')}
          aria-label={t('accounts.edit.chats.messagePlaceholder')}
          className={`min-h-control min-w-0 w-full resize-none overflow-y-hidden rounded-lg border border-line bg-surface-card px-md py-sm type-prose text-content-primary outline-none ${FOCUS_RING} sm:w-auto sm:flex-1`}
        />
        <div className="flex w-full items-center justify-between gap-sm sm:w-auto sm:justify-start">
          <label className="inline-flex min-h-control cursor-pointer items-center rounded-lg border border-line px-md type-label hover:bg-canvas">
            <Icon name="paperclip" size={16} />
            <span className="sr-only">{t('accounts.edit.chats.attachFiles')}</span>
            <input
              type="file"
              multiple
              className="sr-only"
              onChange={(e) => {
                const added = Array.from(e.currentTarget.files ?? []);
                const room = Math.max(0, 10 - files.length);
                setDroppedFiles(Math.max(0, added.length - room));
                setFiles((current) => [...current, ...added].slice(0, 10));
                e.currentTarget.value = '';
              }}
            />
          </label>
          <Button
            variant="primary"
            size="sm"
            type="submit"
            disabled={disabled || pending || (!text.trim() && files.length === 0)}
          >
            {pending ? t('accounts.edit.chats.loadingMore') : t('accounts.edit.chats.send')}
          </Button>
        </div>
      </div>
      {droppedFiles > 0 ? (
        <p role="status" className="mb-0 mt-sm type-caption text-content-muted">
          {t('accounts.edit.chats.filesSkipped', { count: droppedFiles })}
        </p>
      ) : null}
    </form>
  );
}

export function AccountChats({ accountId, overview }: AccountChatsProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<'overview' | 'chats'>('overview');
  const [selection, setSelection] = useState<{ accountId: string; dialog: ChatDialog } | null>(
    null,
  );
  const selected = selection?.accountId === accountId ? selection.dialog : null;
  const [readError, setReadError] = useState(false);
  const [historyError, setHistoryError] = useState(false);
  const [sendPending, setSendPending] = useState(false);
  const acked = useRef(new Set<string>());
  const activeAccount = useRef(accountId);
  useEffect(() => {
    if (activeAccount.current === accountId) return;
    activeAccount.current = accountId;
    setTab('overview');
    setSelection(null);
    setReadError(false);
    setHistoryError(false);
    acked.current.clear();
  }, [accountId]);
  const list = useInfiniteQuery({
    ...accountChatsInfiniteQueryOptions(accountId),
    enabled: tab === 'chats' && activeAccount.current === accountId,
  });
  const dialogs = useMemo(() => list.data?.pages.flatMap((page) => page.items) ?? [], [list.data]);
  const history = useInfiniteQuery({
    ...accountChatHistoryInfiniteQueryOptions(
      accountId,
      selected?.peer_type ?? 'user',
      selected?.peer_id ?? '',
    ),
    enabled: Boolean(selected) && tab === 'chats',
  });
  const messages = useMemo(
    () =>
      history.data?.pages
        .flatMap((page) => page.items)
        .sort((a, b) => a.message_id - b.message_id) ?? [],
    [history.data],
  );
  const markRead = useCallback(
    async (peer: ChatDialog, pageItems: ChatMessage[]) => {
      const maxId = pageItems.reduce(
        (max, item) => Math.max(max, item.message_id),
        peer.last_message?.message_id ?? 0,
      );
      if (!maxId) return;
      const key = `${accountId}:${peer.peer_type}:${peer.peer_id}:${maxId}`;
      if (acked.current.has(key)) return;
      acked.current.add(key);
      try {
        await markAccountChatReadRequest({
          accountId,
          peerType: peer.peer_type,
          peerId: peer.peer_id,
          maxMessageId: maxId,
        });
        setReadError(false);
        await queryClient.invalidateQueries({
          queryKey: accountChatsInfiniteQueryOptions(accountId).queryKey,
        });
      } catch {
        acked.current.delete(key);
        setReadError(true);
      }
    },
    [accountId, queryClient],
  );
  useEffect(() => {
    if (tab !== 'chats' || !selected || !history.data?.pages[0]) return;
    void markRead(selected, history.data.pages[0].items);
  }, [history.data?.pages, markRead, selected, tab]);
  useEffect(() => {
    if (tab === 'chats' && !selected && dialogs.length) {
      setSelection({ accountId, dialog: dialogs[0]! });
    }
  }, [accountId, dialogs, selected, tab]);
  useEffect(() => {
    if (selected) setHistoryError(Boolean(history.error));
  }, [history.error, selected]);
  const refreshList = useCallback(() => {
    void queryClient.invalidateQueries({
      queryKey: accountChatsInfiniteQueryOptions(accountId).queryKey,
    });
  }, [accountId, queryClient]);
  const refreshThread = useCallback(() => {
    if (selected && tab === 'chats')
      void queryClient.invalidateQueries({
        queryKey: accountChatHistoryInfiniteQueryOptions(
          accountId,
          selected.peer_type,
          selected.peer_id,
        ).queryKey,
      });
  }, [accountId, queryClient, selected, tab]);
  const onInbox = useCallback(
    (event: { account_id: string; peer_type: 'user' | 'chat' | 'channel'; peer_id: string }) => {
      if (event.account_id !== accountId) return;
      refreshList();
      if (selected && event.peer_type === selected.peer_type && event.peer_id === selected.peer_id)
        refreshThread();
    },
    [accountId, refreshList, refreshThread, selected],
  );
  useLogEventStream(
    () => {},
    (status) => {
      if (status === 'open') {
        refreshList();
        refreshThread();
      }
    },
    onInbox,
  );
  const submit = async (text: string, files: File[]) => {
    if (!selected) return;
    setSendPending(true);
    try {
      await sendAccountChatMessageRequest({
        accountId,
        peerType: selected.peer_type,
        peerId: selected.peer_id,
        text,
        files,
      });
      await Promise.all([refreshList(), refreshThread()]);
    } finally {
      setSendPending(false);
    }
  };
  const open = (dialog: ChatDialog) => {
    setSelection({ accountId, dialog });
    setTab('chats');
    setReadError(false);
    setHistoryError(false);
  };
  const loadMoreDialogs = () => {
    if (list.hasNextPage && !list.isFetchingNextPage) void list.fetchNextPage();
  };
  const loadOlder = () => {
    if (history.hasNextPage && !history.isFetchingNextPage) void history.fetchNextPage();
  };

  return (
    <div className="flex flex-col gap-lg">
      <div
        role="tablist"
        aria-label={t('accounts.edit.chats.tabs')}
        className="flex w-fit items-center gap-xs rounded-full border border-line bg-surface-card p-xs"
      >
        {(['overview', 'chats'] as const).map((value) => (
          <button
            key={value}
            id={`account-tab-${value}`}
            type="button"
            role="tab"
            aria-selected={tab === value}
            aria-controls={`account-panel-${value}`}
            onClick={() => setTab(value)}
            className={`min-h-touch inline-flex items-center gap-sm rounded-full px-lg text-body font-semibold transition-colors ${tab === value ? 'bg-action-primary text-on-action' : 'text-content-muted hover:bg-canvas hover:text-content-primary'}`}
          >
            {t(`accounts.edit.chats.${value === 'chats' ? 'tab' : 'overview'}`)}
          </button>
        ))}
      </div>
      <section
        id="account-panel-overview"
        role="tabpanel"
        aria-labelledby="account-tab-overview"
        hidden={tab !== 'overview'}
        className={tab === 'overview' ? 'flex flex-col gap-lg' : 'hidden'}
      >
        {overview}
      </section>
      <section
        id="account-panel-chats"
        role="tabpanel"
        aria-labelledby="account-tab-chats"
        hidden={tab !== 'chats'}
        className={tab === 'chats' ? 'flex flex-col gap-md' : 'hidden'}
      >
        <div className="grid grid-cols-1 gap-lg lg:grid-cols-3">
          <section
            className="flex flex-col gap-xs lg:col-span-1"
            aria-label={t('accounts.edit.chats.allConversations')}
          >
            <h2 className="mb-xs type-card-title">{t('accounts.edit.chats.allConversations')}</h2>
            {list.isPending ? (
              <p role="status" className="type-prose text-content-muted">
                {t('accounts.edit.chats.listLoading')}
              </p>
            ) : null}
            {list.isError ? (
              <div role="alert" className="type-prose text-danger">
                {t('accounts.edit.chats.listError')}{' '}
                <Button variant="secondary" size="sm" onClick={refreshList}>
                  {t('accounts.edit.chats.retry')}
                </Button>
              </div>
            ) : null}
            {!list.isPending && !list.isError && dialogs.length === 0 ? (
              <p className="type-prose text-content-muted">{t('accounts.edit.chats.emptyList')}</p>
            ) : null}
            {dialogs.map((dialog) => (
              <button
                key={`${dialog.peer_type}:${dialog.peer_id}`}
                type="button"
                aria-current={
                  selected?.peer_id === dialog.peer_id && selected.peer_type === dialog.peer_type
                    ? 'true'
                    : undefined
                }
                onClick={() => open(dialog)}
                className={`flex min-h-control w-full items-center gap-xs rounded-lg border px-md py-xs text-left transition-colors ${selected?.peer_id === dialog.peer_id && selected.peer_type === dialog.peer_type ? 'border-info-line bg-info-tint' : 'border-line bg-surface-card hover:border-line-strong hover:bg-canvas'}`}
              >
                <span className="flex size-icon shrink-0 items-center justify-center rounded-full bg-canvas text-content-primary type-label font-semibold">
                  {dialog.title.slice(0, 1).toUpperCase()}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-sm">
                    <span className="truncate type-label font-semibold text-content-primary">
                      {dialog.title}
                    </span>
                    {dialog.unread_count > 0 ? (
                      <Badge tone="info">{dialog.unread_count}</Badge>
                    ) : null}
                  </span>
                  <span className="block truncate type-caption text-content-muted">
                    {dialog.last_message?.text ??
                      dialog.last_message?.media?.[0]?.file_name ??
                      t('accounts.edit.chats.noMessages')}
                  </span>
                </span>
                {dialog.is_archived ? (
                  <Badge tone="neutral">{t('accounts.edit.chats.archived')}</Badge>
                ) : null}
              </button>
            ))}
            {list.hasNextPage ? (
              <Button
                variant="secondary"
                size="sm"
                onClick={loadMoreDialogs}
                disabled={list.isFetchingNextPage}
              >
                {list.isFetchingNextPage
                  ? t('accounts.edit.chats.loadingMore')
                  : t('accounts.edit.chats.loadMore')}
              </Button>
            ) : null}
          </section>
          {selected ? (
            <Card className="flex min-h-0 flex-col overflow-hidden lg:col-span-2">
              <header className="flex flex-wrap items-center gap-md border-b border-line-row px-lg py-md">
                <span className="flex size-tile shrink-0 items-center justify-center rounded-full bg-info-tint text-info-strong type-label font-semibold">
                  {selected.title.slice(0, 1).toUpperCase()}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate type-card-title text-content-primary">
                    {selected.title}
                  </span>
                  <span className="type-caption text-content-muted">
                    {t(`accounts.edit.chats.kind.${selected.peer_type}`)}
                    {selected.username ? ` · @${selected.username}` : ''}
                  </span>
                </span>
              </header>
              <div className="flex flex-1 flex-col gap-md overflow-y-auto p-lg" aria-live="polite">
                {readError ? (
                  <div role="alert" className="type-caption text-danger">
                    {t('accounts.edit.chats.readError')}{' '}
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => {
                        acked.current.clear();
                        if (history.data?.pages[0])
                          void markRead(selected, history.data.pages[0].items);
                      }}
                    >
                      {t('accounts.edit.chats.retry')}
                    </Button>
                  </div>
                ) : null}
                {historyError ? (
                  <div role="alert" className="type-prose text-danger">
                    {t('accounts.edit.chats.historyError')}{' '}
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => {
                        setHistoryError(false);
                        void history.refetch();
                      }}
                    >
                      {t('accounts.edit.chats.retry')}
                    </Button>
                  </div>
                ) : null}
                {history.isPending ? (
                  <p role="status" className="type-prose text-content-muted">
                    {t('accounts.edit.chats.listLoading')}
                  </p>
                ) : null}
                {history.hasNextPage ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={loadOlder}
                    disabled={history.isFetchingNextPage}
                  >
                    {history.isFetchingNextPage
                      ? t('accounts.edit.chats.loadingMore')
                      : t('accounts.edit.chats.loadOlder')}
                  </Button>
                ) : null}
                {messages.map((message) => (
                  <ChatMessageView
                    key={message.message_id}
                    message={message}
                    you={t('accounts.edit.chats.you')}
                  />
                ))}
                {!history.isPending && messages.length === 0 ? (
                  <p className="m-auto type-prose text-content-muted">
                    {t('accounts.edit.chats.emptyHistory')}
                  </p>
                ) : null}
              </div>
              <ChatComposer
                onSend={submit}
                pending={sendPending}
                disabled={history.isPending || history.isError}
              />
            </Card>
          ) : (
            <Card className="flex min-h-0 items-center justify-center p-xl type-prose text-content-muted lg:col-span-2">
              {t('accounts.edit.chats.chooseConversation')}
            </Card>
          )}
        </div>
      </section>
    </div>
  );
}
