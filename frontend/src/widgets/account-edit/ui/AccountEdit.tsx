import { useTranslation } from 'react-i18next';

import { Card, ProgressBar } from '@/shared/ui';

import { StatusBadge } from '@/entities/account';
import type { AccountRead } from '@/shared/api';

import { ActionsSection } from './ActionsSection';
import { AccountChats } from './AccountChats';
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
  const overview = (
    <>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <SessionSection account={account} />
        <ProxySection account={account} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <DeviceSection account={account} />
        <SignalsSection account={account} />
      </div>

      {/* The security cards sit together: the cloud password is the other half of
          "who can take this account" that the session card starts, and the actions
          card is what taking it away looks like. */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <TwoFactorSection account={account} />
        <ActionsSection account={account} onBack={onBack} />
      </div>
    </>
  );

  return (
    // Ритм колонки — один зазор, и ставит его колонка: пять детей несли `mb-lg` каждый,
    // кроме последнего, и «кроме последнего» приходилось помнить.
    <div className="tb-fadeup flex max-w-page flex-col gap-4">
      <button
        type="button"
        onClick={onBack}
        // `self-start`: в колонке ребёнок растягивается по умолчанию, а у кнопки «назад»
        // область нажатия должна быть по надписи, а не по всей ширине страницы.
        className="inline-flex self-start items-center gap-2 bg-transparent p-0 text-body font-medium text-content-muted hover:text-content-primary"
      >
        ← {t('accounts.edit.back')}
      </button>

      <Card className="flex flex-wrap items-center gap-4 px-6 py-6">
        <div className="flex size-face shrink-0 items-center justify-center rounded-full bg-info-tint text-h3 font-medium text-info-strong">
          {mono(account)}
        </div>
        <div className="min-w-col flex-1">
          <div className="type-h2">{account.phone ?? account.account_id}</div>
          <div className="type-body text-content-subtle">
            {account.username ? `@${account.username}` : (account.label ?? '—')}
          </div>
        </div>
        <StatusBadge status={account.status} />
        <div className="min-w-col">
          <div className="flex items-center justify-end gap-2">
            <span className="type-body text-content-subtle">{t('accounts.edit.trust')}</span>
            <span className={`text-h3 font-medium ${tTone}`}>{trust}/100</span>
          </div>
          <ProgressBar tone="current" value={trust} className={`mt-2 ${tTone}`} />
        </div>
      </Card>

      <AccountChats accountId={account.account_id} overview={overview} />
    </div>
  );
}
