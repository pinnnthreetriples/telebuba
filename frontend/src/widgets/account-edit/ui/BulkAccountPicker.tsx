import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { accountDisplayName, AccountAvatar, allAccountsQueryOptions } from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { Button, Icon, IconButton, Input, Modal, Spinner } from '@/shared/ui';

// Everything the search box matches on, lowercased once per row rather than per
// keystroke × row. `label` doubles as the row's rendered name.
type Candidate = { account: AccountRead; label: string; haystack: string };

// The bulk editor's "Добавить аккаунты" list: the WHOLE fleet (not the accounts
// page's current cursor page), searchable, with a header checkbox that takes
// every row the search currently shows.
//
// Draft selection, applied on «Добавить»: the strip behind this dialog is the
// batch about to be written, so a mis-tick must be cancellable without having
// touched it.
export function BulkAccountPicker({
  selected,
  onApply,
  onClose,
}: {
  selected: string[];
  onApply: (accountIds: string[]) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<string[]>(selected);
  const [search, setSearch] = useState('');
  const fleet = useQuery(allAccountsQueryOptions());

  const candidates = useMemo<Candidate[]>(
    () =>
      (fleet.data?.items ?? []).map((account) => {
        const label = accountDisplayName(account);
        return {
          account,
          label,
          haystack: [label, account.username, account.phone, account.account_id]
            .filter(Boolean)
            .join(' ')
            .toLowerCase(),
        };
      }),
    [fleet.data],
  );
  const needle = search.trim().toLowerCase();
  const shown = needle ? candidates.filter((row) => row.haystack.includes(needle)) : candidates;

  // Scoped to what the search shows, deliberately: «Выбрать все» under a filter
  // that hides half the fleet must not quietly take the half off screen.
  const shownIds = shown.map((row) => row.account.account_id);
  const allOn = shownIds.length > 0 && shownIds.every((id) => draft.includes(id));
  const someOn = shownIds.some((id) => draft.includes(id));

  const toggle = (accountId: string) => {
    setDraft((prev) =>
      prev.includes(accountId) ? prev.filter((id) => id !== accountId) : [...prev, accountId],
    );
  };
  const toggleAll = () => {
    setDraft((prev) =>
      allOn ? prev.filter((id) => !shownIds.includes(id)) : [...new Set([...prev, ...shownIds])],
    );
  };

  return (
    <Modal onClose={onClose} size="panel" label={t('accounts.bulk.pickTitle')}>
      <div className="flex max-h-dialog flex-col overflow-hidden">
        <div className="flex items-center gap-lg border-b border-line-row px-xl py-xl">
          <div className="min-w-0 flex-1">
            <h2 className="truncate type-dialog-title">{t('accounts.bulk.pickTitle')}</h2>
            <div className="truncate type-prose">
              {t('accounts.bulk.pickCount', { done: draft.length, total: candidates.length })}
            </div>
          </div>
          <IconButton
            size="md"
            onClick={onClose}
            aria-label={t('accounts.profile.close')}
            className="text-title"
          >
            ×
          </IconButton>
        </div>

        <div className="flex flex-col gap-md border-b border-line-row px-xl py-lg">
          <Input
            value={search}
            placeholder={t('accounts.bulk.pickSearch')}
            aria-label={t('accounts.bulk.pickSearch')}
            onChange={(event) => {
              setSearch(event.target.value);
            }}
          />
          <div className="flex items-center justify-between gap-md">
            <button
              type="button"
              role="checkbox"
              aria-checked={allOn ? true : someOn ? 'mixed' : false}
              disabled={shownIds.length === 0}
              onClick={toggleAll}
              className="flex items-center gap-md text-left disabled:opacity-50"
            >
              <span
                className={`flex size-glyph shrink-0 items-center justify-center rounded-sm border ${someOn ? 'border-action-primary bg-action-primary' : 'border-line bg-surface-card'}`}
              >
                {allOn ? (
                  <Icon name="check" size={14} className="stroke-on-action" />
                ) : someOn ? (
                  // Indeterminate is a bar, not a check: a check would claim the
                  // whole visible list is picked when only part of it is.
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 24 24"
                    fill="none"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    className="stroke-on-action"
                    aria-hidden="true"
                  >
                    <path d="M6 12h12" />
                  </svg>
                ) : null}
              </span>
              <span className="type-label">
                {t('accounts.bulk.pickAll', { n: shownIds.length })}
              </span>
            </button>
            {draft.length > 0 && (
              <Button
                size="xs"
                variant="ghost"
                onClick={() => {
                  setDraft([]);
                }}
              >
                {t('accounts.bulk.pickClear')}
              </Button>
            )}
          </div>
        </div>

        <div className="tb-scroll flex-1 overflow-y-auto">
          {fleet.isPending ? (
            <div className="flex justify-center py-empty">
              <Spinner size="lg" />
            </div>
          ) : shown.length === 0 ? (
            <div className="px-xl py-empty text-center type-prose">
              {t('accounts.bulk.pickEmpty')}
            </div>
          ) : (
            shown.map(({ account, label }) => {
              const on = draft.includes(account.account_id);
              return (
                <button
                  key={account.account_id}
                  type="button"
                  role="checkbox"
                  aria-checked={on}
                  onClick={() => {
                    toggle(account.account_id);
                  }}
                  className={`flex w-full items-center gap-md border-b border-line-row px-xl py-sm text-left last:border-b-0 ${on ? 'bg-action-hover' : ''}`}
                >
                  <span
                    className={`flex size-glyph shrink-0 items-center justify-center rounded-sm border ${on ? 'border-action-primary bg-action-primary' : 'border-line bg-surface-card'}`}
                  >
                    {on && <Icon name="check" size={14} className="stroke-on-action" />}
                  </span>
                  <AccountAvatar
                    account={account}
                    className="size-tile shrink-0 rounded-full"
                    fallbackClassName="bg-canvas text-content-muted type-label"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate type-item-title">{label}</span>
                    <span className="block truncate type-caption">
                      {account.phone ?? account.account_id}
                    </span>
                  </span>
                  {account.username != null && account.username !== '' && (
                    <span className="shrink-0 type-caption">@{account.username}</span>
                  )}
                </button>
              );
            })
          )}
        </div>

        <div className="flex items-center justify-end gap-sm border-t border-line-row px-xl py-lg">
          <Button onClick={onClose}>{t('accounts.profile.cancel')}</Button>
          <Button
            variant="primary"
            disabled={draft.length === 0}
            onClick={() => {
              onApply(draft);
            }}
          >
            {t('accounts.bulk.pickApply', { n: draft.length })}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
