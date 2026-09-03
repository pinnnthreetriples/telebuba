import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { DiscoveryAccountOption } from '@/shared/api';
import { cn } from '@/shared/lib/cn';
import { Badge, Icon, Notice } from '@/shared/ui';

import { eligibleAccountIds } from '../model/filters';
import { Eyebrow } from './FormRow';

const P = 'neurocomment.modal.discovery.form.accounts';

type Props = {
  accounts: readonly DiscoveryAccountOption[];
  // The EFFECTIVE pick (already intersected with the eligible set), not the raw form field.
  selected: readonly string[];
  onChange: (ids: string[]) => void;
  loading: boolean;
  errored: boolean;
};

// Мультивыбор аккаунтов для поиска, набран как список каналов в NeuroAccountsModal:
// список раскрывается в потоке, `inert` снимает табстопы, пока он закрыт.
// / The account multi-select; the list expands in flow, `inert` while closed.
export function AccountPicker({ accounts, selected, onChange, loading, errored }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const listId = useId();
  const names = accounts
    .filter((account) => selected.includes(account.account_id))
    .map((account) => account.name);
  const empty = !loading && !errored && eligibleAccountIds(accounts).length === 0;

  const toggle = (id: string) => {
    const chosen = new Set(selected);
    if (chosen.has(id)) chosen.delete(id);
    else chosen.add(id);
    // The list's own order, so the request never depends on click order.
    onChange(accounts.map((account) => account.account_id).filter((next) => chosen.has(next)));
  };

  return (
    <section>
      <Eyebrow
        title={t(`${P}.label`)}
        caption={loading ? t(`${P}.loading`) : t(`${P}.selected`, { count: selected.length })}
      />
      {errored ? (
        <Notice tone="danger">{t(`${P}.loadFailed`)}</Notice>
      ) : empty ? (
        <Notice tone="warning">{t(`${P}.empty`)}</Notice>
      ) : (
        <>
          <button
            type="button"
            aria-haspopup="listbox"
            aria-expanded={open}
            aria-controls={listId}
            disabled={loading}
            onClick={() => {
              setOpen((current) => !current);
            }}
            className="flex w-full items-center justify-between gap-sm rounded-lg border border-line bg-surface-card px-md py-sm text-left type-prose"
          >
            <span className="min-w-0 truncate">
              {names.length > 0 ? names.join(', ') : t(`${P}.trigger`)}
            </span>
            <span className={cn('tb-ddchev flex shrink-0 text-content-subtle', open && 'open')}>
              <Icon name="chevron-down" size={16} />
            </span>
          </button>
          {/* Box styling open-only on purpose: under border-box a collapsed max-height:0
              still reserves its border and padding. See NeuroAccountsModal. */}
          <div
            id={listId}
            role="listbox"
            aria-multiselectable
            aria-label={t(`${P}.label`)}
            inert={!open}
            className={cn(
              'tb-dd',
              open && 'open mt-sm rounded-lg border border-line bg-surface-card p-xs shadow-pop',
            )}
          >
            {accounts.map((account) => {
              const busy = account.busy_reason != null;
              const busyText = busy ? t(`${P}.busy.${account.busy_reason}`) : undefined;
              const picked = selected.includes(account.account_id);
              return (
                <button
                  key={account.account_id}
                  type="button"
                  role="option"
                  aria-selected={picked}
                  disabled={busy}
                  title={busyText}
                  onClick={() => {
                    toggle(account.account_id);
                  }}
                  className="flex w-full items-center justify-between gap-sm rounded-sm px-md py-sm text-left type-prose hover:bg-action-hover disabled:opacity-50"
                >
                  <span className="flex min-w-0 items-center gap-sm">
                    <span className="truncate">{account.name}</span>
                    {account.premium === true ? (
                      <Badge tone="info" size="xs">
                        {t(`${P}.premium`)}
                      </Badge>
                    ) : null}
                  </span>
                  {busy ? (
                    <span className="type-caption">{busyText}</span>
                  ) : picked ? (
                    <Icon name="check" size={14} className="shrink-0" />
                  ) : null}
                </button>
              );
            })}
          </div>
          <p className="mt-tight type-caption">{t(`${P}.premiumHint`)}</p>
        </>
      )}
    </section>
  );
}
