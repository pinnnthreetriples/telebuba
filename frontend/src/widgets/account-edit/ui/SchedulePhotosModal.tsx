import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountScheduledPostsQueryKey,
  scheduleAccountPhotoMutation,
  uploadScheduledMediaMutation,
} from '@/entities/account';
import {
  ScheduleTimeField,
  clampToLead,
  defaultRunAt,
  newBatchId,
  runAtProblem,
  spreadEvery,
  toIso,
  useNow,
} from '@/features/schedule-post';
import {
  Button,
  CloseButton,
  Icon,
  IconButton,
  Input,
  Modal,
  Spinner,
  toastError,
} from '@/shared/ui';

import {
  envelopeMessage,
  isUploadablePhoto,
  PHOTO_MAX_BYTES,
  PHOTO_SUFFIXES,
} from './_channelsShared';
import { DashedAdd, FilePicker } from './_shared';

type Row = {
  key: number;
  file: File;
  url: string;
  runAt: number | null;
  state: 'idle' | 'busy' | 'done' | 'error';
  // Kept once uploaded, so a retry schedules the stored file instead of re-sending it.
  mediaId?: string;
};

const DEFAULT_STEP_MINUTES = 60;
const MAX_STEP_MINUTES = 7 * 24 * 60;

// The typed text stays as typed (clearing "60" to type "30" must work); the plan
// reads the clamped number.
function stepMinutes(text: string): number {
  const value = Math.round(Number(text));
  return Number.isFinite(value) && text.trim() !== ''
    ? Math.min(MAX_STEP_MINUTES, Math.max(1, value))
    : DEFAULT_STEP_MINUTES;
}
// Long enough for the last row's check to be seen before the dialog goes.
const CLOSE_AFTER_MS = 700;

// Profile photos published later, each at its own time. Opened above the profile
// modal like the story composer. Every file is uploaded to the server's store and
// scheduled on its own, strictly one after another, so a refused file (or time)
// fails its row and the rest still go through; after a partial run the done rows
// leave the list and only what failed stays, to be retried or dropped.
export function SchedulePhotosModal({
  accountId,
  onClose,
}: {
  accountId: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const now = useNow();
  const [rows, setRows] = useState<Row[]>([]);
  const [start, setStart] = useState<number | null>(() => defaultRunAt(Date.now()));
  const [stepText, setStepText] = useState(String(DEFAULT_STEP_MINUTES));
  const step = stepMinutes(stepText);
  // One key per dialog: a retry of a row whose answer was lost finds its post
  // instead of making a second one.
  const batchId = useRef(newBatchId());
  const [running, setRunning] = useState(false);
  // Everything went through and the dialog is about to close: nothing may change.
  const [finished, setFinished] = useState(false);
  const locked = running || finished;
  const upload = useMutation(uploadScheduledMediaMutation());
  const schedule = useMutation(scheduleAccountPhotoMutation());
  const nextKey = useRef(0);

  // Object URLs are revoked with the rows that made them, and all at unmount.
  const urls = useRef(new Set<string>());
  useEffect(
    () => () => {
      for (const url of urls.current) URL.revokeObjectURL(url);
    },
    [],
  );
  const closeTimer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    },
    [],
  );

  const add = (picked: File[]) => {
    const kept = picked.filter((file) => {
      if (isUploadablePhoto(file)) return true;
      toastError(
        t('accounts.profile.photoRejected', { name: file.name, mb: PHOTO_MAX_BYTES / 1_000_000 }),
      );
      return false;
    });
    // New files continue the plan: one step after the last time already set.
    // Built outside the state updater, which may run twice and would leak URLs.
    const last = rows.at(-1)?.runAt ?? null;
    const first = last === null ? (start ?? defaultRunAt(Date.now())) : last + step * 60_000;
    const times = spreadEvery(first, kept.length, step);
    const added = kept.map((file, index): Row => {
      const url = URL.createObjectURL(file);
      urls.current.add(url);
      nextKey.current += 1;
      return { key: nextKey.current, file, url, runAt: times[index] ?? null, state: 'idle' };
    });
    setRows((prev) => [...prev, ...added]);
  };

  const remove = (key: number) => {
    const gone = rows.find((row) => row.key === key);
    if (gone) {
      URL.revokeObjectURL(gone.url);
      urls.current.delete(gone.url);
    }
    setRows((prev) => prev.filter((row) => row.key !== key));
  };

  const patch = (key: number, next: Partial<Row>) => {
    setRows((prev) => prev.map((row) => (row.key === key ? { ...row, ...next } : row)));
  };

  // Only the rows still to send: a scheduled one keeps the time the server has.
  const distribute = () => {
    if (start === null) return;
    const open = rows.filter((row) => row.state !== 'done').map((row) => row.key);
    const times = spreadEvery(start, open.length, step);
    setRows((prev) =>
      prev.map((row) => {
        const index = open.indexOf(row.key);
        return index < 0 ? row : { ...row, runAt: times[index] ?? row.runAt };
      }),
    );
  };

  const pending = rows.filter((row) => row.state !== 'done');
  const ready = pending.length > 0 && pending.every((row) => runAtProblem(row.runAt, now) === null);

  const submit = async () => {
    setRunning(true);
    let failed = 0;
    const done = new Set<number>();
    for (const row of pending) {
      if (row.runAt === null) continue;
      patch(row.key, { state: 'busy' });
      try {
        const mediaId =
          row.mediaId ?? (await upload.mutateAsync({ body: { file: row.file } })).media_id;
        patch(row.key, { mediaId });
        await schedule.mutateAsync({
          path: { account_id: accountId },
          body: {
            media_id: mediaId,
            // Re-checked at send time: the rows ahead may have taken a while.
            run_at: toIso(clampToLead(row.runAt, Date.now())),
            filename: row.file.name,
            batch_id: batchId.current,
            // Stable per row: a retry after a lost answer (even with a moved time)
            // finds its post, which the server then moves, never a second copy.
            client_key: `row${String(row.key)}`,
          },
        });
        patch(row.key, { state: 'done' });
        done.add(row.key);
      } catch (error) {
        // The global mutation toast carries the reason; the row stays for a retry.
        failed += 1;
        // The server swept the stored file (a dialog left open past its grace):
        // the retry uploads it again instead of naming a file that is gone.
        const stale = envelopeMessage(error) === 'scheduled_media_missing';
        patch(row.key, stale ? { state: 'error', mediaId: undefined } : { state: 'error' });
      }
    }
    setRunning(false);
    void queryClient.invalidateQueries({
      queryKey: accountScheduledPostsQueryKey({ path: { account_id: accountId } }),
    });
    if (failed === 0) {
      setFinished(true);
      closeTimer.current = window.setTimeout(onClose, CLOSE_AFTER_MS);
      return;
    }
    // The scheduled rows leave; what failed stays, to be fixed or dropped and retried.
    for (const row of pending) {
      if (!done.has(row.key)) continue;
      URL.revokeObjectURL(row.url);
      urls.current.delete(row.url);
    }
    setRows((prev) => prev.filter((row) => !done.has(row.key)));
  };

  // Photos picked and not yet scheduled (after a partial run, the ones that failed), or
  // a changed step. The first time is not counted: its default follows the clock.
  const dirty = !finished && (rows.length > 0 || stepText !== String(DEFAULT_STEP_MINUTES));

  return (
    // The exits are locked while a row is being sent, for the same reason the story
    // composer locks them: unmounting mid-request loses which rows made it.
    <Modal
      onClose={onClose}
      dirty={dirty}
      locked={running}
      size="form"
      label={t('accounts.schedule.photosTitle')}
    >
      {(close) => (
        <div className="tb-scroll max-h-dialog overflow-y-auto px-6 py-6">
          <div className="mb-4 flex items-center justify-between">
            <span className="type-h2">{t('accounts.schedule.photosTitle')}</span>
            <CloseButton
              onClick={close}
              disabled={locked}
              aria-label={t('accounts.addStory.close')}
            />
          </div>
          <div className="mb-4 type-body text-content-subtle">
            {t('accounts.schedule.photosHint')}
          </div>

          <div className="mb-4 flex flex-wrap items-end gap-3">
            <ScheduleTimeField
              value={start}
              onChange={setStart}
              now={now}
              label={t('accounts.schedule.firstAt')}
              disabled={locked}
            />
            <label className="flex flex-col gap-2">
              <span className="type-body-medium text-content-secondary">
                {t('accounts.schedule.everyMinutes')}
              </span>
              <Input
                type="number"
                size="sm"
                min={1}
                max={MAX_STEP_MINUTES}
                value={stepText}
                disabled={locked}
                onChange={(event) => {
                  setStepText(event.target.value);
                }}
                onBlur={() => {
                  setStepText(String(step));
                }}
              />
            </label>
            <Button
              size="sm"
              disabled={locked || rows.length === 0 || start === null}
              onClick={distribute}
            >
              {t('accounts.schedule.distribute')}
            </Button>
          </div>

          <ul className="flex flex-col gap-2">
            {rows.map((row) => (
              <li
                key={row.key}
                className="flex items-center gap-3 rounded-md border border-line px-3 py-2"
              >
                <img
                  src={row.url}
                  alt=""
                  className="size-tile shrink-0 rounded-sm border border-black/5 object-cover"
                />
                <div className="min-w-0 flex-1">
                  <ScheduleTimeField
                    value={row.runAt}
                    onChange={(runAt) => {
                      patch(row.key, { runAt, state: row.state === 'error' ? 'idle' : row.state });
                    }}
                    now={now}
                    size="sm"
                    label={t('accounts.schedule.timeFor', { name: row.file.name })}
                    disabled={locked || row.state === 'done'}
                  />
                </div>
                <span className="flex shrink-0 items-center justify-center gap-1">
                  {row.state !== 'idle' && (
                    <span className="sr-only">{t(`accounts.schedule.rowStatus.${row.state}`)}</span>
                  )}
                  {row.state === 'busy' && <Spinner />}
                  {row.state === 'done' && (
                    <Icon name="check" size={16} className="stroke-success-deep" />
                  )}
                  {row.state === 'error' && (
                    <Icon name="x-circle" size={16} className="stroke-danger" />
                  )}
                  {/* A refused file can be dropped, or the dialog could never finish. */}
                  {(row.state === 'idle' || row.state === 'error') && (
                    <IconButton
                      size="sm"
                      shape="circle"
                      aria-label={t('accounts.schedule.removeFile', { name: row.file.name })}
                      disabled={locked}
                      onClick={() => {
                        remove(row.key);
                      }}
                    >
                      <Icon name="close" size={16} />
                    </IconButton>
                  )}
                </span>
              </li>
            ))}
          </ul>
          {!locked && (
            <div className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(104px,1fr))] gap-3">
              <FilePicker accept={PHOTO_SUFFIXES.join(',')} multiple onPick={add}>
                {(open) => (
                  <DashedAdd ratio="1" label={t('accounts.schedule.addPhotos')} onClick={open} />
                )}
              </FilePicker>
            </div>
          )}

          <div className="mt-6 flex justify-end gap-2">
            <Button onClick={close} disabled={locked}>
              {t('accounts.addStory.cancel')}
            </Button>
            <Button
              variant="primary"
              loading={running}
              disabled={!ready || locked}
              onClick={() => {
                void submit();
              }}
            >
              {t('accounts.schedule.submitPhotos', { count: pending.length })}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
