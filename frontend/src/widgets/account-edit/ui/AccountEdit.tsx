import { useTranslation } from 'react-i18next';

import { Card } from '@/shared/ui';

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
  return t >= 70 ? 'text-success-deep' : t >= 45 ? 'text-warning-deep' : 'text-danger';
}

// The design's account-edit view (reached by clicking a row): an always-visible
// hero header above six collapsible cards — session, proxy, device, signals,
// 2FA, actions — each owning its own state/mutations. All wired to /api/v1.
export function AccountEdit({ account, onBack }: { account: AccountRead; onBack: () => void }) {
  const { t } = useTranslation();
  const trust = account.trust_score ?? 0;
  const tTone = trustTone(trust);

  return (
    <div className="tb-fadeup max-w-page">
      <button
        type="button"
        onClick={onBack}
        className="mb-lg inline-flex items-center gap-sm bg-transparent p-0 text-body font-medium text-content-muted hover:text-content-primary"
      >
        ← {t('accounts.edit.back')}
      </button>

      <Card className="mb-lg flex flex-wrap items-center gap-lg px-xl py-xl">
        <div className="flex size-face shrink-0 items-center justify-center rounded-full bg-info-tint text-title font-semibold text-info-strong">
          {mono(account)}
        </div>
        <div className="min-w-col flex-1">
          <div className="type-dialog-title">{account.phone ?? account.account_id}</div>
          <div className="type-prose">
            {account.username ? `@${account.username}` : (account.label ?? '—')}
          </div>
        </div>
        <StatusBadge status={account.status} />
        <div className="min-w-col">
          <div className="flex items-center justify-end gap-sm">
            <span className="type-prose">{t('accounts.edit.trust')}</span>
            <span className={`text-title font-bold ${tTone}`}>{trust}/100</span>
          </div>
          <div className="mt-tight h-meter overflow-hidden rounded-full bg-canvas">
            <div
              className={`h-full rounded-full bg-current ${tTone}`}
              style={{ width: `${String(trust)}%` }}
            />
          </div>
        </div>
      </Card>

      <div className="mb-lg grid grid-cols-1 md:grid-cols-2 gap-lg">
        <SessionSection account={account} />
        <ProxySection account={account} />
      </div>

      <div className="mb-lg grid grid-cols-1 md:grid-cols-2 gap-lg">
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
