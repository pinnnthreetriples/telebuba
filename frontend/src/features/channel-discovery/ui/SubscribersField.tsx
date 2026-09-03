import { useTranslation } from 'react-i18next';

import { Input } from '@/shared/ui';

import { boundsInverted, type DiscoveryFormState } from '../model/discovery';

const P = 'neurocomment.modal.discovery.form';

type Props = {
  form: DiscoveryFormState;
  onChange: (form: DiscoveryFormState) => void;
};

// Пара границ «от — до». Рендерится внутрь `Row`, поэтому строка ошибки переносится на
// всю ширину через `basis-full`. / The min–max pair; the error wraps onto its own line.
export function SubscribersField({ form, onChange }: Props) {
  const { t } = useTranslation();
  const inverted = boundsInverted(form);
  return (
    <>
      <div className="flex items-center gap-sm">
        <Input
          size="xs"
          type="number"
          min={0}
          className="w-number tabular-nums"
          aria-label={t(`${P}.minSubscribers`)}
          placeholder="0"
          invalid={inverted}
          value={form.minSubscribers}
          onChange={(event) => {
            onChange({ ...form, minSubscribers: event.target.value });
          }}
        />
        <span className="type-caption">—</span>
        <Input
          size="xs"
          type="number"
          min={0}
          className="w-number tabular-nums"
          aria-label={t(`${P}.maxSubscribers`)}
          placeholder="∞"
          invalid={inverted}
          value={form.maxSubscribers}
          onChange={(event) => {
            onChange({ ...form, maxSubscribers: event.target.value });
          }}
        />
      </div>
      {/* The API refuses members_min > members_max, and canSubmit blocks it — without
          this the Search button would just go dead naming no field. */}
      {inverted ? (
        <p className="basis-full type-caption text-danger">{t(`${P}.boundsInverted`)}</p>
      ) : null}
    </>
  );
}
