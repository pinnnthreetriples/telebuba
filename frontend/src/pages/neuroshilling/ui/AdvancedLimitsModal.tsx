import { useTranslation } from 'react-i18next';

import { Badge, Button, HelpHint, Input, Modal, Switch } from '@/shared/ui';

import type { SetupDraft } from './setupDraft';
import { useNumberField } from './useNumberField';
import {
  clampInt,
  MAX_MESSAGES_PER_CHAT_PER_DAY,
  MAX_MESSAGES_PER_HOUR,
  MAX_TOTAL_PER_ACCOUNT,
} from './setupDraft';

// Одна строка с числовым полем. Здесь, а не в `shared/ui`: три таких живут в этом
// диалоге и больше нигде, а числового поля в системе нет.
function NumberRow({
  label,
  hint,
  value,
  min,
  max,
  placeholder,
  disabled,
  onChange,
}: {
  label: string;
  hint: string;
  value: string;
  min: number;
  max: number;
  placeholder?: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  const field = useNumberField(
    Number(value),
    (raw) => clampInt(raw, min, max),
    (next) => {
      onChange(String(next));
    },
  );
  // Пустое поле «Всего на аккаунт» означает «без лимита» и уезжает как null, поэтому у
  // него собственный текст: подставлять туда число нельзя.
  const nullable = placeholder !== undefined;
  return (
    <div className="flex items-center gap-md border-b border-line-row py-md">
      {/* `span`, а не `label`: имя полю даёт его собственный `aria-label`, и второй
          элемент-подпись сделал бы это имя неоднозначным. */}
      <span className="min-w-0 flex-1 text-body">{label}</span>
      <HelpHint text={hint} />
      <Input
        size="xs"
        className="w-number tabular-nums"
        type="number"
        min={min}
        max={max}
        value={nullable ? value : field.value}
        disabled={disabled}
        placeholder={placeholder}
        aria-label={label}
        onChange={(event) => {
          if (nullable) onChange(event.target.value);
          else field.onChange(event.target.value);
        }}
        onBlur={nullable ? undefined : field.onBlur}
      />
    </div>
  );
}

// Расширенные лимиты: то, что защищает аккаунты от флуд-бана, и резерв, который
// подхватывает роль забаненного.
//
// Отдельный диалог, а не сворачивающаяся панель внутри настроек: в макете строка «Лимиты
// и резерв» показывает СВОДКУ и кнопку «Настроить», и это правильное разделение — цифры
// здесь трогают редко, а место в общем окне они занимали постоянно. Правки пишутся прямо
// в тот же черновик, поэтому «Применить» только закрывает: сохраняет всё равно подвал
// диалога настроек, одной кнопкой на оба черновика.
export function AdvancedLimitsModal({
  draft,
  onDraft,
  reserveCount,
  live,
  onClose,
}: {
  draft: SetupDraft;
  onDraft: (draft: SetupDraft) => void;
  reserveCount: number;
  live: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Modal onClose={onClose} size="form" label={t('neuroshilling.setup.advanced.title')}>
      <div className="border-b border-line-row px-2xl pb-lg pt-xl type-dialog-title">
        {t('neuroshilling.setup.advanced.title')}
      </div>

      <div className="px-2xl py-sm">
        <NumberRow
          label={t('neuroshilling.setup.perHour.label')}
          hint={t('neuroshilling.setup.perHour.hint')}
          value={String(draft.messagesPerHour)}
          min={1}
          max={MAX_MESSAGES_PER_HOUR}
          disabled={live}
          onChange={(value) => {
            onDraft({
              ...draft,
              messagesPerHour: clampInt(Number(value), 1, MAX_MESSAGES_PER_HOUR),
            });
          }}
        />
        <NumberRow
          label={t('neuroshilling.setup.perChat.label')}
          hint={t('neuroshilling.setup.perChat.hint')}
          value={String(draft.messagesPerChatPerDay)}
          min={0}
          max={MAX_MESSAGES_PER_CHAT_PER_DAY}
          disabled={live}
          onChange={(value) => {
            onDraft({
              ...draft,
              messagesPerChatPerDay: clampInt(Number(value), 0, MAX_MESSAGES_PER_CHAT_PER_DAY),
            });
          }}
        />
        <NumberRow
          label={t('neuroshilling.setup.total.label')}
          hint={t('neuroshilling.setup.total.hint')}
          placeholder={t('neuroshilling.setup.total.unlimited')}
          value={draft.totalPerAccount === null ? '' : String(draft.totalPerAccount)}
          min={1}
          max={MAX_TOTAL_PER_ACCOUNT}
          disabled={live}
          onChange={(value) => {
            // Пусто — «без потолка», и уезжает как null. Ноль отверг бы `ge=1` на
            // проводе, поэтому опустошённое поле не должно им становиться.
            onDraft({
              ...draft,
              totalPerAccount:
                value.trim() === '' ? null : clampInt(Number(value), 1, MAX_TOTAL_PER_ACCOUNT),
            });
          }}
        />

        <div className="flex items-center gap-md py-md">
          <span className="min-w-0 flex-1 text-body">{t('neuroshilling.setup.reserve.label')}</span>
          <HelpHint text={t('neuroshilling.setup.reserve.hint')} />
          {/* Пул КАК ОН ЕСТЬ сейчас, а не как был собран ростер: у повышенного аккаунта
              флаг резерва снимается, поэтому число падает на каждой замене, и ноль —
              то предупреждение, ради которого оно здесь. */}
          <Badge className="tabular-nums">
            {t('neuroshilling.setup.reserve.count', { n: reserveCount })}
          </Badge>
          <Switch
            checked={draft.reserveEnabled}
            disabled={live}
            label={t('neuroshilling.setup.reserve.label')}
            onChange={(value) => {
              onDraft({ ...draft, reserveEnabled: value });
            }}
          />
        </div>
      </div>

      <div className="flex items-center justify-end gap-sm border-t border-line-row px-2xl py-lg">
        <Button variant="primary" size="sm" onClick={onClose}>
          {t('neuroshilling.setup.advanced.done')}
        </Button>
      </div>
    </Modal>
  );
}
