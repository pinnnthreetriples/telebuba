import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { WarmingWave } from '@/shared/api';
import { Button, EmptyState, Modal, ModalHeader } from '@/shared/ui';

import { warmingWavesQueryOptions } from '../api/warming.queries';

function shortDate(at: string | null | undefined, locale: string): string {
  if (!at) return '—';
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleDateString(locale, { day: '2-digit', month: '2-digit' });
}

function WaveLine({ wave }: { wave: WarmingWave }) {
  const { t, i18n } = useTranslation();
  const start = shortDate(wave.started_at, i18n.language);
  const end = wave.ended_at ? shortDate(wave.ended_at, i18n.language) : null;
  const days =
    wave.worked_days == null
      ? t('warming.waves.daysUnknown')
      : t('warming.waves.days', { count: wave.worked_days, target: wave.target_days ?? '—' });
  return (
    <li className="border-b border-canvas py-3 last:border-b-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="type-h3">{t('warming.waves.wave', { wave: wave.wave })}</span>
        <span className="type-small tabular-nums">
          {end ? `${start} — ${end}` : t('warming.waves.since', { date: start })}
        </span>
      </div>
      <div className="mt-1 type-small">
        {days} ·{' '}
        {wave.end_reason
          ? t(`warming.waves.end.${wave.end_reason}`)
          : t('warming.waves.end.running')}
      </div>
    </li>
  );
}

// Every wave of the account's current life, oldest first: start, end, worked days, and
// why it ended. Read from the lifecycle journal, so it matches the survival panel.
function WavesModal({
  accountId,
  name,
  onClose,
}: {
  accountId: string;
  name: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const query = useQuery(warmingWavesQueryOptions({ path: { account_id: accountId } }));
  const waves = query.data?.waves ?? [];
  const title = t('warming.waves.title', { name });
  return (
    <Modal onClose={onClose} size="confirm" label={title}>
      <ModalHeader title={title} subtitle={t('warming.waves.subtitle')} />
      <div className="px-6 pb-6 pt-2">
        {waves.length > 0 ? (
          <ul className="m-0 list-none p-0">
            {waves.map((wave) => (
              <WaveLine key={wave.wave} wave={wave} />
            ))}
          </ul>
        ) : (
          <EmptyState size="sm">
            {query.isPending ? t('warming.waves.loading') : t('warming.waves.empty')}
          </EmptyState>
        )}
      </div>
    </Modal>
  );
}

// «Волна 2 · с 14.10»: shown from the second wave on. A click opens the account's wave
// history. The first wave needs no chip — every card's day bar already is its story.
export function WaveChip({
  accountId,
  name,
  wave,
  startedAt,
}: {
  accountId: string;
  name: string;
  wave?: number | null;
  startedAt?: string | null;
}) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  if (wave == null || wave <= 1) return null;
  return (
    <>
      <Button
        size="sm"
        variant="ghost"
        onClick={() => {
          setOpen(true);
        }}
        className="rounded-full bg-info-tint text-info-strong"
      >
        {t('warming.waves.chip', { wave, date: shortDate(startedAt, i18n.language) })}
      </Button>
      {open ? (
        <WavesModal
          accountId={accountId}
          name={name}
          onClose={() => {
            setOpen(false);
          }}
        />
      ) : null}
    </>
  );
}
