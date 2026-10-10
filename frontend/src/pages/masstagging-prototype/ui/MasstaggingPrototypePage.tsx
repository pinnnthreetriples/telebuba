// PROTOTYPE — throwaway. Layout variants of the Масстегинг page, switchable
// via `?variant=D|A|B|C` on /prototype/masstagging. Mock data only, no API calls.
// Delete once a variant wins (see ../NOTES.md).
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useRouterState } from '@tanstack/react-router';
import { useEffect, useState } from 'react';

import { accountDisplayName, allAccountsQueryOptions } from '@/entities/account';
import type { AccountRead } from '@/shared/api';

import { Icon } from '@/shared/ui';

import { ACCOUNTS, type Draft, INITIAL_DRAFT, type MockAccount, setFleet } from './model';
import { NAME as NAME_A, VariantA } from './VariantA';
import { NAME as NAME_B, VariantB } from './VariantB';
import { NAME as NAME_C, VariantC } from './VariantC';
import { NAME as NAME_D, VariantD } from './VariantD';

function toMock(account: AccountRead): MockAccount {
  return {
    id: account.account_id,
    name: accountDisplayName(account),
    phone: account.phone ?? '',
    username: account.username ?? '',
    country: '',
    premium: account.premium === true,
    proxyOk: true,
    ageDays: 0,
    spamblock: account.spam_status === 'limited',
    busy: false,
  };
}

const VARIANTS = [
  { key: 'D', name: NAME_D, View: VariantD },
  { key: 'A', name: NAME_A, View: VariantA },
  { key: 'B', name: NAME_B, View: VariantB },
  { key: 'C', name: NAME_C, View: VariantC },
] as const;

function PrototypeSwitcher({ current }: { current: number }) {
  const navigate = useNavigate();
  const go = (delta: number) => {
    const next = VARIANTS[(current + delta + VARIANTS.length) % VARIANTS.length];
    void navigate({ to: '/prototype/masstagging', search: { variant: next?.key ?? 'D' } });
  };
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const el = event.target;
      if (
        el instanceof HTMLInputElement ||
        el instanceof HTMLTextAreaElement ||
        (el instanceof HTMLElement && el.isContentEditable)
      )
        return;
      if (event.key === 'ArrowLeft') go(-1);
      if (event.key === 'ArrowRight') go(1);
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
    };
  });
  if (!import.meta.env.DEV) return null;
  const variant = VARIANTS[current];
  return (
    <div className="fixed bottom-4 left-1/2 z-toast flex -translate-x-1/2 items-center gap-3 rounded-full bg-term px-2 py-1 text-term-text shadow-pop">
      <button
        type="button"
        aria-label="Предыдущий вариант"
        className="flex size-icon items-center justify-center rounded-full"
        onClick={() => {
          go(-1);
        }}
      >
        <Icon name="chevron-left" size={16} />
      </button>
      <span className="text-body font-medium">
        {variant?.key} — {variant?.name}
      </span>
      <button
        type="button"
        aria-label="Следующий вариант"
        className="flex size-icon items-center justify-center rounded-full"
        onClick={() => {
          go(1);
        }}
      >
        <Icon name="chevron-right" size={16} />
      </button>
    </div>
  );
}

export function MasstaggingPrototypePage() {
  const search = useRouterState({ select: (state) => state.location.search });
  const key = 'variant' in search && typeof search.variant === 'string' ? search.variant : 'D';
  const index = Math.max(
    VARIANTS.findIndex((v) => v.key === key),
    0,
  );
  const [draft, setDraft] = useState<Draft>(INITIAL_DRAFT);
  const [running, setRunning] = useState(false);
  const fleet = useQuery(allAccountsQueryOptions());
  const items = fleet.data?.items ?? [];
  if (items.length > 0) setFleet(items.map(toMock));
  // The mock ids mean nothing to the real fleet: start from its first two accounts.
  const firstIds = items.slice(0, 2).map((account) => account.account_id).join(',');
  useEffect(() => {
    if (firstIds === '') return;
    setDraft((prev) =>
      prev.accountIds.every((id) => ACCOUNTS.some((account) => account.id === id))
        ? prev
        : { ...prev, accountIds: firstIds.split(',') },
    );
  }, [firstIds]);
  const patch = (next: Partial<Draft>) => {
    setDraft((prev) => ({ ...prev, ...next }));
  };
  const toggle = () => {
    setRunning((on) => !on);
  };
  const View = VARIANTS[index]?.View ?? VariantD;
  return (
    <>
      <View draft={draft} patch={patch} running={running} onToggleRun={toggle} />
      <PrototypeSwitcher current={index} />
    </>
  );
}
