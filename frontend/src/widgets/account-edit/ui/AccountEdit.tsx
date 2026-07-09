import { useTranslation } from 'react-i18next';

import { StatusBadge } from '@/entities/account';
import type { AccountRead } from '@/shared/api';

import { ActionsSection } from './ActionsSection';
import { DeviceSection } from './DeviceSection';
import { ProxySection } from './ProxySection';
import { SessionSection } from './SessionSection';
import { SignalsSection } from './SignalsSection';

function mono(account: AccountRead): string {
  return (account.phone ?? account.account_id).replace(/\D/g, '').slice(-2) || '#';
}

// Trust Score is real (computed by the backend); the 3-tier colour band mirrors
// the design's thresholds.
function trustColor(t: number): string {
  return t >= 70 ? '#12a150' : t >= 45 ? '#e08700' : '#e5372a';
}

// The design's account-edit view (reached by clicking a row): an always-visible
// hero header above five collapsible cards — session, proxy, device, signals,
// actions — each owning its own state/mutations. All wired to /api/v1.
export function AccountEdit({ account, onBack }: { account: AccountRead; onBack: () => void }) {
  const { t } = useTranslation();
  const trust = account.trust_score ?? 0;
  const tColor = trustColor(trust);

  return (
    <div className="tb-fadeup max-w-[960px]">
      <button
        type="button"
        onClick={onBack}
        className="mb-4 inline-flex items-center gap-[6px] bg-transparent p-0 text-[13px] font-medium text-ink-muted hover:text-ink"
      >
        ← {t('accounts.edit.back')}
      </button>

      <div className="mb-[14px] flex flex-wrap items-center gap-[18px] rounded-2xl border border-line bg-white px-5 py-[18px]">
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-primary-tint text-[17px] font-semibold text-primary">
          {mono(account)}
        </div>
        <div className="min-w-[150px] flex-1">
          <div className="text-[18px] font-bold">{account.phone ?? account.account_id}</div>
          <div className="text-[12px] text-ink-subtle">
            {account.username ? `@${account.username}` : (account.label ?? '—')}
          </div>
        </div>
        <StatusBadge status={account.status} />
        <div className="min-w-[130px]">
          <div className="flex items-center justify-end gap-2">
            <span className="text-[12px] text-ink-muted">{t('accounts.edit.trust')}</span>
            <span className="text-[18px] font-bold" style={{ color: tColor }}>
              {trust}/100
            </span>
          </div>
          <div className="mt-[6px] h-[6px] overflow-hidden rounded-full bg-track">
            <div
              className="h-full rounded-full"
              style={{ width: `${String(trust)}%`, background: tColor }}
            />
          </div>
        </div>
      </div>

      <div className="mb-[14px] grid grid-cols-2 gap-[14px]">
        <SessionSection account={account} />
        <ProxySection account={account} />
      </div>

      <div className="mb-[14px] grid grid-cols-2 gap-[14px]">
        <DeviceSection account={account} />
        <SignalsSection account={account} />
      </div>

      <ActionsSection account={account} onBack={onBack} />
    </div>
  );
}
