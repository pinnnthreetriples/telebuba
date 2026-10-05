import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountProfileSnapshotQueryKey,
  accountScheduledPostsQueryKey,
  accountScheduledPostsQueryOptions,
  accountsQueryKey,
  cancelScheduledPostMutation,
  rescheduleScheduledPostMutation,
} from '@/entities/account';
import {
  ScheduleTimeField,
  clampToLead,
  runAtProblem,
  toIso,
  useNow,
} from '@/features/schedule-post';
import type { ScheduledPostRead } from '@/shared/api';
import { formatLocalDateTime, formatRelativeTo, useLogEventStream } from '@/shared/lib';
import { Badge, type BadgeTone, Button, ConfirmModal, Icon } from '@/shared/ui';

import { profileCodeText, tileStyle } from './_profileShared';

type ScheduledPostState = ScheduledPostRead['state'];

// Refetch while something is still due, as the backstop for a dropped SSE frame.
const OPEN_POLL_MS = 30_000;

const TONE: Record<ScheduledPostState, BadgeTone> = {
  pending: 'info',
  processing: 'info',
  done: 'success',
  failed: 'danger',
  cancelled: 'neutral',
  missed: 'warning',
  ambiguous: 'warning',
};

// States the operator can still move or cancel. `processing` is the post going out
// right now, and the server answers 409 for it.
const EDITABLE: ReadonlySet<ScheduledPostState> = new Set([
  'pending',
  'missed',
  'failed',
  'ambiguous',
]);

// A post that failed on a Telegram wait failed because the wait outlasted its grace
// window; the generic "retry in N s" copy would promise a retry that will not come.
const TIMED_WAITS: ReadonlySet<string> = new Set(['flood_wait', 'slow_mode_wait', 'premium_wait']);

function errorText(code: string, t: Parameters<typeof profileCodeText>[1]): string {
  return TIMED_WAITS.has(code) ? t('accounts.schedule.waitOutlasted') : profileCodeText(code, t);
}

// A cancel or move that finds the post already gone (404) or going out (409) has
// nothing left to do: the dialog closes and the list catches up.
function settledElsewhere(error: unknown): boolean {
  const code = (error as { error?: { code?: string } } | null)?.error?.code;
  return code === 'not_found' || code === 'conflict';
}

// The account's scheduled photos or stories, under the grid they will join. Only
// the posts of one `kind`, and nothing at all while there are none.
export function ScheduledPostsList({
  accountId,
  kind,
}: {
  accountId: string;
  kind: ScheduledPostRead['kind'];
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const path = { path: { account_id: accountId } };
  const listKey = accountScheduledPostsQueryKey(path);
  const list = useQuery({
    ...accountScheduledPostsQueryOptions(path),
    refetchInterval: (query) =>
      (query.state.data?.items ?? []).some(
        (item) => item.state === 'pending' || item.state === 'processing',
      )
        ? OPEN_POLL_MS
        : false,
  });
  const [confirm, setConfirm] = useState<ScheduledPostRead | null>(null);
  const cancel = useMutation(cancelScheduledPostMutation());

  useLogEventStream((entry) => {
    if (entry.account_id !== accountId || !entry.event.startsWith('account_scheduled_post_')) {
      return;
    }
    void queryClient.invalidateQueries({ queryKey: listKey });
    if (entry.event === 'account_scheduled_post_published') {
      // The post is on Telegram now: the grid above and the table's avatar changed.
      void queryClient.invalidateQueries({ queryKey: accountProfileSnapshotQueryKey(path) });
      void queryClient.invalidateQueries({ queryKey: accountsQueryKey() });
    }
  });

  const items = (list.data?.items ?? []).filter((item) => item.kind === kind);
  if (items.length === 0) return null;

  return (
    <section className="mt-6" aria-label={t('accounts.schedule.listTitle')}>
      <div className="mb-3 flex items-center gap-2">
        <Icon name="clock" size={14} className="text-content-muted" />
        <span className="type-body-medium text-content-secondary">
          {t('accounts.schedule.listTitle')}
        </span>
        <Badge size="xs">{items.length}</Badge>
      </div>
      <ul className="flex flex-col gap-2">
        {items.map((item) => (
          <ScheduledRow
            key={item.post_id}
            accountId={accountId}
            item={item}
            onCancel={() => {
              setConfirm(item);
            }}
          />
        ))}
      </ul>
      {confirm !== null && (
        <ConfirmModal
          title={t('accounts.schedule.cancelTitle')}
          body={t('accounts.schedule.cancelBody')}
          confirmLabel={t('accounts.schedule.cancelConfirm')}
          cancelLabel={t('accounts.profile.cancel')}
          onClose={() => {
            setConfirm(null);
          }}
          onConfirm={() =>
            cancel
              .mutateAsync({ path: { account_id: accountId, post_id: confirm.post_id } })
              .catch((error: unknown) => {
                if (!settledElsewhere(error)) throw error;
              })
              .finally(() => {
                void queryClient.invalidateQueries({ queryKey: listKey });
              })
          }
        />
      )}
    </section>
  );
}

function ScheduledRow({
  accountId,
  item,
  onCancel,
}: {
  accountId: string;
  item: ScheduledPostRead;
  onCancel: () => void;
}) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const now = useNow();
  const [editing, setEditing] = useState<number | null>(null);
  const reschedule = useMutation(rescheduleScheduledPostMutation());
  const runAt = new Date(item.run_at).getTime();
  const nextAt = new Date(item.next_attempt_at).getTime();
  const editable = EDITABLE.has(item.state);
  // The editor belongs to an editable post: one that started publishing (SSE or
  // poll) closes it rather than offering a Save the server would refuse.
  const open = editing !== null && editable;
  const rowRef = useRef<HTMLLIElement>(null);
  const wasOpen = useRef(false);
  useEffect(() => {
    // Focus goes back to «Перенести» once the editor closes, however it closed.
    if (wasOpen.current && !open) rowRef.current?.querySelector('button')?.focus();
    wasOpen.current = open;
  }, [open]);
  const close = () => {
    setEditing(null);
  };
  // A post that stopped being editable drops the draft, so it cannot reopen later
  // (a flood wait sends a publishing post back to pending) with a stale time.
  useEffect(() => {
    if (!editable) setEditing(null);
  }, [editable]);

  const save = () => {
    if (editing === null || runAtProblem(editing, now) !== null) return;
    void reschedule
      .mutateAsync({
        path: { account_id: accountId, post_id: item.post_id },
        // Clamped at send time: `now` ticks every 30 s, the server's clock does not wait.
        body: { run_at: toIso(clampToLead(editing, Date.now())) },
      })
      .then(close)
      .catch((error: unknown) => {
        // The global mutation toast reports it; the field stays open to retry,
        // unless there is nothing left to move.
        if (settledElsewhere(error)) close();
      })
      .finally(() => {
        void queryClient.invalidateQueries({
          queryKey: accountScheduledPostsQueryKey({ path: { account_id: accountId } }),
        });
      });
  };

  // "не раньше 14:30" while it waits; the retry moment once a Telegram limit moved it.
  const when =
    item.state === 'pending' && nextAt > runAt
      ? t('accounts.schedule.retryAt', { when: formatLocalDateTime(nextAt, i18n.language) })
      : t('accounts.schedule.notBefore', { when: formatLocalDateTime(runAt, i18n.language) });

  return (
    <li ref={rowRef} className="flex items-start gap-3 rounded-md border border-line px-3 py-2">
      <div
        className="size-tile shrink-0 overflow-hidden rounded-sm border border-black/5"
        style={tileStyle(item.thumb_url, '1')}
      >
        {item.media_kind === 'video' && (
          <span className="flex h-full w-full items-center justify-center text-content-muted">
            <Icon name="video" size={16} />
          </span>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="type-body-medium">{when}</span>
          <Badge tone={TONE[item.state]} size="xs">
            {t(`accounts.schedule.state.${item.state}`)}
          </Badge>
        </div>
        <div className="mt-px truncate type-small">
          {item.state === 'pending'
            ? formatRelativeTo(nextAt, now, i18n.language)
            : item.error_code
              ? errorText(item.error_code, t)
              : (item.filename ?? '')}
          {item.media_count > 1 && ` · ${t('accounts.schedule.collage', { n: item.media_count })}`}
        </div>
        {open && (
          <form
            className="mt-2 flex flex-wrap items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              save();
            }}
            onKeyDown={(event) => {
              // Esc cancels the edit only; the profile dialog around it stays open.
              if (event.key !== 'Escape') return;
              event.stopPropagation();
              // Not mid-save: a refusal must find the field still open to retry.
              if (!reschedule.isPending) close();
            }}
          >
            <ScheduleTimeField
              value={editing}
              onChange={setEditing}
              now={now}
              size="sm"
              label={t('accounts.schedule.newTime')}
              autoFocus
              readOnly={reschedule.isPending}
            />
            <Button
              type="submit"
              size="sm"
              variant="primary"
              loading={reschedule.isPending}
              disabled={runAtProblem(editing, now) !== null}
            >
              {t('accounts.schedule.save')}
            </Button>
            <Button size="sm" disabled={reschedule.isPending} onClick={close}>
              {t('accounts.profile.cancel')}
            </Button>
          </form>
        )}
      </div>
      {editable && !open && (
        <div className="flex shrink-0 items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setEditing(Math.max(runAt, now + 15 * 60_000));
            }}
          >
            {t('accounts.schedule.reschedule')}
          </Button>
          <Button size="sm" variant="ghost" onClick={onCancel}>
            {t('accounts.schedule.cancel')}
          </Button>
        </div>
      )}
    </li>
  );
}
