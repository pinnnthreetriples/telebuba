import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { AccountTwoFactorCreated } from '@/shared/api';
import { mutationErrorText, useClearedTimeouts } from '@/shared/lib';
import { Button, Icon, IconButton, Notice } from '@/shared/ui';

import type { BulkTwofaRow } from './useBulkTwofa';

// A CSV cell: quote everything and double the quotes inside, so a display name
// carrying a comma cannot split the row the password sits in.
const cell = (value: string) => `"${value.replace(/"/g, '""')}"`;

// The RESULT phase of the wizard's cloud-password step, split out of
// TwoFactorBulkStep: the plaintext table is the whole reason the step exists and
// it has its own clipboard/CSV machinery, which does not belong beside the
// selection form.
//
// The clipboard contract is TwoFactorSection's, verbatim in behaviour: the label
// is driven by how the promise SETTLED (never by having called it), a rejection
// stays on screen instead of auto-clearing, and where `navigator.clipboard` is
// absent altogether — any non-secure context — the buttons come off and the
// panel says to select and copy by hand. These passwords are the only copy.
export function TwoFactorBulkResults({
  rows,
  label,
  onDone,
}: {
  rows: BulkTwofaRow[];
  label: (accountId: string) => string;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const later = useClearedTimeouts();
  const [copyState, setCopyState] = useState<Record<string, 'done' | 'failed'>>({});
  const clipboard: Clipboard | undefined = navigator.clipboard;
  // A predicate, not a plain `!== null`: without it every read below needs a
  // `?? ''` fallback that can never run, and unreachable branches in the one
  // panel that carries the plaintext are exactly the wrong thing to leave lying.
  const created = rows.filter((row): row is BulkTwofaRow & { created: AccountTwoFactorCreated } => {
    return row.created !== null;
  });

  const copy = (key: string, text: string) => {
    if (!clipboard) return;
    void clipboard.writeText(text).then(
      () => {
        setCopyState((prev) => ({ ...prev, [key]: 'done' }));
        later(() => {
          // Only clears the state it was scheduled for: a successful copy's
          // timer must not erase a LATER rejection, which is the one signal
          // that the operator's single copy never reached the clipboard.
          setCopyState((prev) => {
            if (prev[key] !== 'done') return prev;
            const next = { ...prev };
            delete next[key];
            return next;
          });
        }, 2400);
      },
      () => {
        setCopyState((prev) => ({ ...prev, [key]: 'failed' }));
      },
    );
  };

  const allText = created
    .map((row) => `${label(row.accountId)}\t${row.created.password}`)
    .join('\n');

  const download = () => {
    const csv = created
      .map((row) => `${cell(label(row.accountId))},${cell(row.created.password)}`)
      .join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'telebuba-2fa.csv';
    anchor.click();
    // Revoked on the next tick, not inline: `click()` only STARTS the download,
    // and a URL torn down in the same frame can be gone before the browser has
    // read the blob — which loses the only file copy of these passwords.
    later(() => {
      URL.revokeObjectURL(url);
    }, 0);
  };

  return (
    <>
      {/* Notice sets no role of its own — it is usually prose that was on screen
          before the operator looked. This one reports an outcome that expires
          with the dialog, so it asks to be announced. */}
      <Notice tone="danger" role="alert">
        {t('accounts.addWizard.twofaOnceWarn')}
      </Notice>
      {/* The 16px to the Notice above is worn HERE: a caller may not hand
          Card/Notice an outer margin (classMerge.test). */}
      <div className="mb-md mt-lg flex items-center justify-between gap-md">
        <span className="type-caption">
          {t('accounts.addWizard.twofaSelected', { done: created.length, total: rows.length })}
        </span>
        <span className="flex shrink-0 gap-sm">
          {clipboard ? (
            <Button
              size="xs"
              onClick={() => {
                copy('all', allText);
              }}
            >
              {copyState.all === 'done'
                ? t('accounts.edit.twofaCopied')
                : t('accounts.addWizard.twofaCopyAll')}
            </Button>
          ) : null}
          <Button size="xs" onClick={download}>
            {t('accounts.addWizard.twofaDownload')}
          </Button>
        </span>
      </div>
      {clipboard ? null : (
        <div className="mb-md type-caption">{t('accounts.edit.twofaCopyManual')}</div>
      )}
      {copyState.all === 'failed' ? (
        <div className="mb-md type-caption text-danger">{t('accounts.edit.twofaCopyFailed')}</div>
      ) : null}
      <div className="overflow-hidden rounded-lg border border-line">
        {rows.map((row) =>
          row.created ? (
            <div
              key={row.accountId}
              className="flex items-start gap-md border-b border-line-row px-md py-sm last:border-b-0"
            >
              <span className="w-stamp shrink-0 break-words type-item-title">
                {label(row.accountId)}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block break-all font-mono type-value">{row.created.password}</span>
                {row.created.stored === false ? (
                  <span className="mt-hair block type-caption text-warning-deep">
                    {t('accounts.edit.twofaStoreFailed')}
                  </span>
                ) : null}
                {row.created.confirmed === false ? (
                  <span className="mt-hair block type-caption text-warning-deep">
                    {t('accounts.edit.twofaUnconfirmed')}
                  </span>
                ) : null}
                {copyState[row.accountId] === 'failed' ? (
                  <span className="mt-hair block type-caption text-danger">
                    {t('accounts.edit.twofaCopyFailed')}
                  </span>
                ) : null}
              </span>
              {clipboard ? (
                <IconButton
                  size="sm"
                  aria-label={
                    copyState[row.accountId] === 'done'
                      ? t('accounts.edit.twofaCopied')
                      : t('accounts.edit.twofaCopy')
                  }
                  onClick={() => {
                    copy(row.accountId, row.created?.password ?? '');
                  }}
                >
                  {copyState[row.accountId] === 'done' ? (
                    <Icon name="check" size={14} className="stroke-success-deep" />
                  ) : (
                    <svg
                      width="14"
                      height="14"
                      viewBox="0 0 24 24"
                      fill="none"
                      strokeWidth="1.8"
                      className="stroke-line-strong"
                      aria-hidden="true"
                    >
                      <path d="M9 9h10v10H9zM5 15H4V4h11v1" />
                    </svg>
                  )}
                </IconButton>
              ) : null}
            </div>
          ) : row.state === 'error' ? (
            <div
              key={row.accountId}
              className="flex items-start gap-md border-b border-line-row bg-danger-tint px-md py-sm last:border-b-0"
            >
              <span className="w-stamp shrink-0 break-words type-item-title">
                {label(row.accountId)}
              </span>
              {/* The refusal codes already have Russian text — the same resolver
                  the global mutation toast uses. Inventing copy here would be a
                  second, drifting table for one dialog. */}
              <span className="min-w-0 flex-1 type-caption text-danger-deep">
                {mutationErrorText(row.error)}
              </span>
            </div>
          ) : (
            // Still `queued`: the operator pressed «Остановить» and the batch
            // never reached this account. Its own row rather than the refusal
            // one above — that branch would run `mutationErrorText` over a null
            // error and accuse Telegram of refusing a request nobody sent.
            <div
              key={row.accountId}
              className="flex items-start gap-md border-b border-line-row px-md py-sm last:border-b-0"
            >
              <span className="w-stamp shrink-0 break-words type-item-title">
                {label(row.accountId)}
              </span>
              <span className="min-w-0 flex-1 type-caption">
                {t('accounts.addWizard.twofaNotRun')}
              </span>
            </div>
          ),
        )}
      </div>
      <div className="mt-xl flex justify-end gap-sm">
        <Button variant="primary" onClick={onDone}>
          {t('accounts.addWizard.done')}
        </Button>
      </div>
    </>
  );
}
