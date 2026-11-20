import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { HEADING_ICON_TILE } from '@/shared/design-system';
import { Badge, Button, Icon, Input, Modal, Notice, SegmentedControl, Switch } from '@/shared/ui';

import {
  type FolderDevice,
  type FolderFinish,
  type FolderPace,
  type Persona,
  PERSONAS,
  type PersonaMix,
  type WarmingFolder,
} from '../model/folders';

import { RangeSlider } from './RangeSlider';

const MIN = 1;
const MAX = 30;
const PRESETS = [7, 14, 21, 30];
const TICKS = [MIN, ...PRESETS];
const DELAY_PRESETS = [1, 3, 6, 12, 24];
const DEVICES: FolderDevice[] = ['desktop', 'mobile', 'primary'];
const PACES: FolderPace[] = ['inherit', 'careful', 'normal', 'fast'];
const FINISHES: FolderFinish[] = ['keep', 'handoff'];

// Готовые смеси типов: одна кнопка — один тип, «Смешанные» открывает проценты.
type MixPreset = Persona | 'mixed';
const MIX_PRESETS: MixPreset[] = ['calm', 'normal', 'active', 'mixed'];
const ONLY: Record<Persona, PersonaMix> = {
  calm: { calm: 100, normal: 0, active: 0 },
  normal: { calm: 0, normal: 100, active: 0 },
  active: { calm: 0, normal: 0, active: 100 },
};
const DEFAULT_MIX: PersonaMix = { calm: 30, normal: 50, active: 20 };

function mixPreset(mix: PersonaMix): MixPreset {
  const only = PERSONAS.find((p) => mix[p] === 100);
  return only ?? 'mixed';
}

function Section({
  title,
  hint,
  badge,
  children,
}: {
  title: string;
  hint?: string;
  badge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="mb-6">
      <div className="mb-2 flex items-center gap-2 type-body-medium">
        {title}
        {badge}
      </div>
      {children}
      {hint ? <div className="mt-2 type-small text-content-muted">{hint}</div> : null}
    </div>
  );
}

// «Прогрев пачки»: настройки папки. Верх — то же, что окно «Прогрев аккаунта»
// (длительность, тип, устройство, первый заход), ниже — темп (сейчас он общий на весь
// прогрев) и что делать с аккаунтом, когда папка его догрела. Тонкая настройка
// действий по папке — следующий этап.
export function FolderSettingsModal({
  initial,
  members,
  onClose,
  onSave,
  onDelete,
}: {
  initial: WarmingFolder;
  // Сколько аккаунтов уже в папке: правка скажет, когда что применится к ним.
  members: number;
  onClose: () => void;
  onSave: (folder: WarmingFolder) => void;
  onDelete?: () => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<WarmingFolder>(initial);
  const set = (patch: Partial<WarmingFolder>) => {
    setDraft((d) => ({ ...d, ...patch }));
  };
  const creating = initial.id === '';
  const title = creating
    ? t('warming.folders.modal.createTitle')
    : t('warming.folders.modal.editTitle');
  const preset = mixPreset(draft.mix);
  const mixSum = PERSONAS.reduce((sum, p) => sum + draft.mix[p], 0);
  const valid = draft.name.trim() !== '' && mixSum === 100;
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial);
  const dayCount = (n: number) => `${String(n)} ${t('warming.days.label', { count: n })}`;
  const prototype = (
    <Badge size="xs" tone="warning">
      {t('warming.folders.prototype')}
    </Badge>
  );

  return (
    <Modal onClose={onClose} dirty={dirty} size="form" label={title}>
      {(close) => (
        <div className="p-6">
          <div className="mb-1 flex items-start gap-3">
            <div className={HEADING_ICON_TILE}>
              <Icon name="folder" size={16} />
            </div>
            <div className="flex-1 type-h2">{title}</div>
          </div>
          <div className="mb-6 type-body text-content-muted">
            {creating
              ? t('warming.folders.modal.createSubtitle')
              : t('warming.folders.modal.editSubtitle', { count: members })}
          </div>

          <Section title={t('warming.folders.modal.name')}>
            <Input
              value={draft.name}
              placeholder={t('warming.folders.modal.namePlaceholder')}
              aria-label={t('warming.folders.modal.name')}
              onChange={(event) => {
                set({ name: event.target.value });
              }}
            />
          </Section>

          <div className="mb-6 text-center">
            <div className="text-h1 font-medium leading-none text-action-primary">{draft.days}</div>
            <div className="mt-1 type-body text-content-muted">
              {t('warming.days.label', { count: draft.days })}
            </div>
          </div>
          <div className="mb-6">
            <RangeSlider
              value={draft.days}
              min={MIN}
              max={MAX}
              ticks={TICKS}
              label={t('warming.days.title')}
              valueText={dayCount}
              onChange={(days) => {
                set({ days });
              }}
            />
          </div>
          <SegmentedControl
            variant="outline"
            className="mb-6"
            value={String(draft.days)}
            options={PRESETS.map((n) => ({ value: String(n), label: dayCount(n) }))}
            onChange={(value) => {
              set({ days: Number(value) });
            }}
          />

          <Section
            title={t('warming.folders.modal.personas')}
            hint={t('warming.folders.modal.personasHint')}
          >
            <SegmentedControl
              variant="outline"
              value={preset}
              ariaLabel={t('warming.folders.modal.personas')}
              options={MIX_PRESETS.map((p) => ({
                value: p,
                label: t(`warming.folders.mixPreset.${p}`),
              }))}
              onChange={(p) => {
                set({ mix: p === 'mixed' ? DEFAULT_MIX : ONLY[p] });
              }}
            />
            {preset === 'mixed' ? (
              <div className="mt-3 grid grid-cols-3 gap-2">
                {PERSONAS.map((p) => (
                  <label key={p} className="flex flex-col gap-1 type-small text-content-muted">
                    {t(`warming.persona.${p}.name`)}, %
                    <Input
                      type="number"
                      min={0}
                      max={100}
                      value={draft.mix[p]}
                      invalid={mixSum !== 100}
                      onChange={(event) => {
                        set({ mix: { ...draft.mix, [p]: Number(event.target.value) || 0 } });
                      }}
                    />
                  </label>
                ))}
                {mixSum === 100 ? null : (
                  <div className="col-span-3 type-small text-danger">
                    {t('warming.folders.modal.mixSum', { sum: mixSum })}
                  </div>
                )}
              </div>
            ) : null}
          </Section>

          <Section title={t('warming.device.label')}>
            <SegmentedControl
              variant="outline"
              value={draft.device}
              ariaLabel={t('warming.device.label')}
              options={DEVICES.map((d) => ({ value: d, label: t(`warming.folders.device.${d}`) }))}
              onChange={(device) => {
                set({ device });
              }}
            />
            <div className="mt-2 type-small text-content-muted">
              {t(`warming.folders.deviceHint.${draft.device}`)}
            </div>
            {draft.device === 'mobile' ? (
              <div className="mt-2">
                <Notice tone="warning">{t('warming.folders.modal.mobileLimits')}</Notice>
              </div>
            ) : null}
          </Section>

          <Section title={t('warming.delay.label')}>
            <SegmentedControl
              variant="outline"
              value={String(draft.delayHours)}
              options={DELAY_PRESETS.map((n) => ({
                value: String(n),
                label: t('warming.delay.short', { count: n }),
              }))}
              onChange={(value) => {
                set({ delayHours: Number(value) });
              }}
            />
            <div className="mt-3 flex items-center gap-3">
              <Switch
                checked={draft.spread}
                label={t('warming.folders.modal.spread')}
                onChange={(spread) => {
                  set({ spread });
                }}
              />
              <div>
                <div className="type-body">{t('warming.folders.modal.spread')}</div>
                <div className="type-small text-content-muted">
                  {t(
                    draft.spread
                      ? 'warming.folders.modal.spreadOn'
                      : 'warming.folders.modal.spreadOff',
                    { count: draft.delayHours },
                  )}
                </div>
              </div>
            </div>
          </Section>

          <Section
            title={t('warming.pace.title')}
            badge={prototype}
            hint={t(`warming.folders.paceHint.${draft.pace}`)}
          >
            <SegmentedControl
              variant="outline"
              value={draft.pace}
              ariaLabel={t('warming.pace.title')}
              options={PACES.map((p) => ({ value: p, label: t(`warming.folders.pace.${p}`) }))}
              onChange={(pace) => {
                set({ pace });
              }}
            />
          </Section>

          <Section
            title={t('warming.folders.modal.finish')}
            hint={t(`warming.folders.finishHint.${draft.finish}`)}
          >
            <SegmentedControl
              variant="outline"
              value={draft.finish}
              ariaLabel={t('warming.folders.modal.finish')}
              options={FINISHES.map((f) => ({ value: f, label: t(`warming.folders.finish.${f}`) }))}
              onChange={(finish) => {
                set({ finish });
              }}
            />
          </Section>

          {!creating && members > 0 && dirty ? (
            <div className="mb-6">
              <Notice tone="info">{t('warming.folders.modal.impact', { count: members })}</Notice>
            </div>
          ) : null}

          <div className="flex flex-wrap justify-end gap-2">
            {onDelete && !creating ? (
              <Button
                variant="danger"
                className="mr-auto"
                onClick={() => {
                  onDelete();
                  onClose();
                }}
              >
                {t('warming.folders.modal.delete')}
              </Button>
            ) : null}
            <Button
              variant="primary"
              disabled={!valid}
              onClick={() => {
                onSave({ ...draft, name: draft.name.trim() });
                onClose();
              }}
            >
              {creating ? t('warming.folders.modal.create') : t('warming.folders.modal.save')}
            </Button>
            <Button onClick={close}>{t('warming.days.cancel')}</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
