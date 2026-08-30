import { useTranslation } from 'react-i18next';

import type { NeuroshillingCampaign } from '@/shared/api';
import { Badge, type BadgeTone } from '@/shared/ui';

type Status = NonNullable<NeuroshillingCampaign['status']>;

// Тон статуса и его подпись — ОДИН набор на страницу.
//
// Редизайн показывает статус кампании в трёх местах сразу: в списке сайдбара, в шапке
// конвейера и в строке доски работ. Таблица тона была скопирована в каждое, а до этого
// жила в двух копиях как набор классов `text-*` — и копии уже расходились: у одной не
// было `stopping`. Статус один, значит и решение о его цвете одно.
export function CampaignStatusBadge({
  status,
  plain = false,
}: {
  status: Status;
  // Без заливки: точка и подпись краской тона. Так статус набран в карточке сайдбара —
  // и здесь, и у неврокомментинга, — потому что рядом с именем кампании залитая плашка
  // спорит с ним за вес. В таблице и в шапке конвейера заливка остаётся: там подпись
  // стоит одна в ячейке, и опереться ей не на что.
  //
  // Проп, а не вторая копия таблицы тонов: решение о цвете статуса остаётся одно, ради
  // чего этот файл и заведён.
  plain?: boolean;
}) {
  const { t } = useTranslation();
  const label = t(`neuroshilling.campaign.status.${status}`);
  if (plain) {
    return (
      <span
        className={`inline-flex items-center gap-tight type-caption font-medium ${INK[TONE[status]]}`}
      >
        {/* `bg-current` — точка не может разойтись с собственной подписью. */}
        <span className="size-dot shrink-0 rounded-full bg-current" />
        {label}
      </span>
    );
  }
  return (
    <Badge dot tone={TONE[status]}>
      {label}
    </Badge>
  );
}

// Краска подписи для варианта без заливки — те же рунги, которыми `badgeTone` красит
// текст плашки, поэтому один и тот же статус выглядит одинаково в обеих формах.
const INK: Record<BadgeTone, string> = {
  neutral: 'text-content-muted',
  info: 'text-info-strong',
  success: 'text-success-deep',
  warning: 'text-warning-deep',
  danger: 'text-danger-deep',
};

const TONE: Record<Status, BadgeTone> = {
  idle: 'neutral',
  running: 'success',
  stopping: 'warning',
  done: 'info',
  failed: 'danger',
};
