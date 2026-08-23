import { useTranslation } from 'react-i18next';

import { StatusBadge } from '@/entities/account';
import type { AccountRead } from '@/shared/api';

import { ActionsSection } from './ActionsSection';
import { DeviceSection } from './DeviceSection';
import { ProxySection } from './ProxySection';
import { SessionSection } from './SessionSection';
import { SignalsSection } from './SignalsSection';
import { TwoFactorSection } from './TwoFactorSection';

function mono(account: AccountRead): string {
  return (account.phone ?? account.account_id).replace(/\D/g, '').slice(-2) || '#';
}

// Trust Score is real (computed by the backend); the 3-tier band mirrors the
// design's thresholds, as text tokens — the bar takes `bg-current` off the same
// class, so the bar and the number cannot disagree.
function trustTone(t: number): string {
  return t >= 70 ? 'text-success' : t >= 45 ? 'text-warning-strong' : 'text-danger';
}

// The design's account-edit view (reached by clicking a row): an always-visible
// hero header above six collapsible cards — session, proxy, device, signals,
// 2FA, actions — each owning its own state/mutations. All wired to /api/v1.
export function AccountEdit({ account, onBack }: { account: AccountRead; onBack: () => void }) {
  const { t } = useTranslation();
  const trust = account.trust_score ?? 0;
  const tTone = trustTone(trust);

  return (
    <div className="tb-fadeup max-w-[960px]">
      <button
        type="button"
        onClick={onBack}
        className="mb-4 inline-flex items-center gap-sm bg-transparent p-0 text-[13px] font-medium text-ink-muted hover:text-ink"
      >
        ← {t('accounts.edit.back')}
      </button>

      <div className="mb-[14px] flex flex-wrap items-center gap-lg rounded-card border border-line bg-white px-5 py-[18px]">
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-primary-tint text-[16px] font-semibold text-primary">
          {mono(account)}
        </div>
        <div className="min-w-[150px] flex-1">
          <div className="text-[16px] font-bold">{account.phone ?? account.account_id}</div>
          <div className="text-[12.5px] text-ink-subtle">
            {account.username ? `@${account.username}` : (account.label ?? '—')}
          </div>
        </div>
        <StatusBadge status={account.status} />
        <div className="min-w-[130px]">
          <div className="flex items-center justify-end gap-sm">
            <span className="text-[12.5px] text-ink-muted">{t('accounts.edit.trust')}</span>
            <span className={`text-[16px] font-bold ${tTone}`}>{trust}/100</span>
          </div>
          <div className="mt-[6px] h-[6px] overflow-hidden rounded-full bg-track">
            <div
              className={`h-full rounded-full bg-current ${tTone}`}
              style={{ width: `${String(trust)}%` }}
            />
          </div>
        </div>
      </div>

      <div className="mb-[14px] grid grid-cols-1 md:grid-cols-2 gap-lg">
        <SessionSection account={account} />
        <ProxySection account={account} />
      </div>

      <div className="mb-[14px] grid grid-cols-1 md:grid-cols-2 gap-lg">
        <DeviceSection account={account} />
        <SignalsSection account={account} />
      </div>

      {/* The security cards sit together: the cloud password is the other half of
          "who can take this account" that the session card starts, and the actions
          card is what taking it away looks like. No bottom margin — the last row
          owns the page's bottom edge. */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-lg">
        <TwoFactorSection account={account} />
        <ActionsSection account={account} onBack={onBack} />
      </div>
    </div>
  );
}
