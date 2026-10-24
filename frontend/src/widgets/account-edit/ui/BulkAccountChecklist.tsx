import { useTranslation } from 'react-i18next';

import { Icon } from '@/shared/ui';

// The add wizard's "which of the just-added accounts" list, shared by the cloud
// password and passkey steps: a select-all header over one row per account.
//
// The square box of the slice's own checkbox row (_CheckRow), copied rather than
// reused: these rows are two lines — a display name over the file or number the
// operator recognises it by — and _CheckRow takes a single `label: string`.
//
// Written out at both sites rather than hoisted into `BOX_ON`/`BOX_OFF` consts,
// and that is not a style preference: `contrast.test.ts` reads the fill and the
// ink out of the SAME class list, so a fill behind a hoisted name is invisible to
// it and `stroke-on-action` floats up to whatever the row sits on — the header's
// `bg-surface`, which it reads at 1.05:1. `_CheckRow.tsx` writes it inline for
// the same reason and is the shape the gate has a passing fixture for.
export function BulkAccountChecklist({
  accountIds,
  sources,
  selected,
  onChange,
  label,
}: {
  accountIds: string[];
  sources: Record<string, string>;
  selected: string[];
  onChange: (next: string[]) => void;
  label: (accountId: string) => string;
}) {
  const { t } = useTranslation();
  const allOn = selected.length === accountIds.length && accountIds.length > 0;
  const toggle = (accountId: string) => {
    onChange(
      selected.includes(accountId)
        ? selected.filter((id) => id !== accountId)
        : [...selected, accountId],
    );
  };

  return (
    <div className="overflow-hidden rounded-md border border-line">
      <div className="flex items-center gap-3 border-b border-line bg-surface px-3 py-2">
        <button
          type="button"
          role="checkbox"
          aria-checked={allOn ? true : selected.length > 0 ? 'mixed' : false}
          onClick={() => {
            onChange(allOn ? [] : accountIds);
          }}
          className="flex items-center gap-3 text-left"
        >
          <span
            className={`flex size-glyph shrink-0 items-center justify-center rounded-sm border ${selected.length > 0 ? 'border-action-primary bg-action-primary' : 'border-line bg-surface-card'}`}
          >
            {allOn ? (
              <Icon name="check" size={14} className="stroke-on-fill" />
            ) : selected.length > 0 ? (
              // Indeterminate is a bar, not a check: a check here would claim
              // the whole batch is picked when only part of it is.
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                strokeWidth="2.5"
                strokeLinecap="round"
                className="stroke-on-fill"
                aria-hidden="true"
              >
                <path d="M6 12h12" />
              </svg>
            ) : null}
          </span>
          <span className="type-body-medium text-content-secondary">
            {t('accounts.addWizard.twofaSelectAll')}
          </span>
        </button>
        <span className="ml-auto shrink-0 type-small">
          {t('accounts.addWizard.twofaSelected', {
            done: selected.length,
            total: accountIds.length,
          })}
        </span>
      </div>
      {accountIds.map((accountId) => {
        const on = selected.includes(accountId);
        return (
          <button
            key={accountId}
            type="button"
            role="checkbox"
            aria-checked={on}
            onClick={() => {
              toggle(accountId);
            }}
            className="flex w-full items-center gap-3 border-b border-canvas px-3 py-2 text-left last:border-b-0"
          >
            <span
              className={`flex size-glyph shrink-0 items-center justify-center rounded-sm border ${on ? 'border-action-primary bg-action-primary' : 'border-line bg-surface-card'}`}
            >
              {on && <Icon name="check" size={14} className="stroke-on-fill" />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate type-body-medium">{label(accountId)}</span>
              <span className="block truncate font-mono type-small">
                {sources[accountId] ?? ''}
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
