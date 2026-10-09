import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { DiscoveryAccountOption } from '@/shared/api';
import {
  Button,
  HelpHint,
  Input,
  SectionLabel,
  SegmentedControl,
  Select,
  SettingRow,
  Switch,
  Textarea,
} from '@/shared/ui';

import {
  EXCLUDE_TOGGLES,
  LAST_SEEN,
  MESSAGE_TOGGLES,
  MODE_LIMITS,
  PARSER_MODES,
  parseSources,
  PROFILE_TOGGLES,
  SKIP_TOGGLES,
  type ParserForm,
  type ParserToggle,
} from '../model/userParser';
import { AccountPicker } from './AccountPicker';

const P = 'userParser.form';
const H = 'userParser.help';

// Число, которое можно стереть, не получив ноль до потери фокуса.
function NumberInput({
  value,
  min,
  max,
  step = 1,
  label,
  onCommit,
}: {
  value: number;
  min: number;
  max: number;
  step?: number;
  label: string;
  onCommit: (next: number) => void;
}) {
  const [text, setText] = useState<string | null>(null);
  return (
    <Input
      size="sm"
      type="number"
      className="w-number tabular-nums"
      min={min}
      max={max}
      step={step}
      aria-label={label}
      value={text ?? String(value)}
      onChange={(event) => {
        const raw = event.target.value;
        setText(raw);
        const next = Number(raw);
        if (raw.trim() !== '' && Number.isFinite(next)) {
          onCommit(Math.min(max, Math.max(min, next)));
        }
      }}
      onBlur={() => {
        setText(null);
      }}
    />
  );
}

// Подпись поля над ним (не строкой формы) — с той же «?» справа.
function FieldLabel({
  htmlFor,
  label,
  caption,
  help,
}: {
  htmlFor: string;
  label: string;
  caption?: string;
  help: { text: string; example?: string };
}) {
  return (
    <div className="mb-2 flex flex-wrap items-center gap-2">
      <label htmlFor={htmlFor} className="type-body-medium text-content-secondary">
        {label}
      </label>
      <HelpHint text={help.text} example={help.example} />
      {caption === undefined ? null : <span className="type-small">{caption}</span>}
    </div>
  );
}

type Props = {
  form: ParserForm;
  formId: string;
  accounts: readonly DiscoveryAccountOption[];
  accountsLoading: boolean;
  accountsErrored: boolean;
  accountIds: string[];
  // Каналы кампании, из которой открыт парсер: их можно подставить в источники одной кнопкой.
  campaignChannels: readonly string[];
  presets: readonly string[];
  onApplyPreset: (name: string) => void;
  onSavePreset: () => void;
  onChange: (form: ParserForm) => void;
  onSubmit: () => void;
};

export function UserParserForm({
  form,
  formId,
  accounts,
  accountsLoading,
  accountsErrored,
  accountIds,
  campaignChannels,
  presets,
  onApplyPreset,
  onSavePreset,
  onChange,
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  const sourcesId = useId();
  const keywordsId = useId();
  const blacklistId = useId();
  const stopId = useId();
  const sources = parseSources(form.sources);
  const counted = form.mode !== 'members';
  // Подсказка: фраза и пример. Пример необязателен — пустой не рисуется.
  const help = (key: string) => {
    const example = t(`${H}.${key}.ex`, { defaultValue: '' });
    return { text: t(`${H}.${key}.text`), example: example === '' ? undefined : example };
  };

  const toggle = (key: ParserToggle) => (
    <SettingRow key={key} label={t(`${P}.toggles.${key}`)} help={help(key)}>
      <Switch
        checked={form.toggles[key]}
        label={t(`${P}.toggles.${key}`)}
        onChange={(on) => {
          onChange({ ...form, toggles: { ...form.toggles, [key]: on } });
        }}
      />
    </SettingRow>
  );

  return (
    <form
      id={formId}
      className="flex flex-col gap-6"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <section>
        <SectionLabel title={t(`${P}.sections.query`)} />
        <SettingRow first label={t(`${P}.presets.label`)} help={help('presets')}>
          <div className="flex items-center gap-2">
            <div className="w-menu">
              <Select
                value=""
                placeholder={t(`${P}.presets.placeholder`)}
                emptyLabel={t(`${P}.presets.empty`)}
                ariaLabel={t(`${P}.presets.label`)}
                options={presets.map((name) => ({ value: name, label: name }))}
                onChange={onApplyPreset}
              />
            </div>
            <Button type="button" size="sm" onClick={onSavePreset}>
              {t(`${P}.presets.save`)}
            </Button>
          </div>
        </SettingRow>
        <SettingRow label={t(`${P}.mode.label`)} help={help(`mode.${form.mode}`)}>
          <SegmentedControl
            variant="pill"
            value={form.mode}
            ariaLabel={t(`${P}.mode.label`)}
            options={PARSER_MODES.map((mode) => ({ value: mode, label: t(`${P}.mode.${mode}`) }))}
            onChange={(mode) => {
              onChange({ ...form, mode });
            }}
          />
        </SettingRow>

        <div className="mt-4 grid gap-4 sm:grid-cols-2 sm:gap-6">
          <section className="min-w-0">
            <FieldLabel
              htmlFor={sourcesId}
              label={t(`${P}.sources.${form.mode}`)}
              caption={t(`${P}.sourcesCount`, { count: sources.length })}
              help={help('sources')}
            />
            <Textarea
              id={sourcesId}
              value={form.sources}
              placeholder={t(`${P}.sourcesPlaceholder.${form.mode}`)}
              onChange={(event) => {
                onChange({ ...form, sources: event.target.value });
              }}
            />
            <Button
              type="button"
              size="sm"
              variant="secondary"
              className="mt-2"
              disabled={campaignChannels.length === 0}
              onClick={() => {
                onChange({
                  ...form,
                  sources: [...new Set([...sources, ...campaignChannels])].join('\n'),
                });
              }}
            >
              {t(`${P}.fromCampaign`, { count: campaignChannels.length })}
            </Button>
          </section>

          <div className="flex min-w-0 flex-col gap-4">
            <AccountPicker
              accounts={accounts}
              selected={accountIds}
              loading={accountsLoading}
              errored={accountsErrored}
              onChange={(ids) => {
                onChange({ ...form, accountIds: ids });
              }}
            />
            {counted ? (
              <section className="min-w-0">
                <FieldLabel
                  htmlFor={keywordsId}
                  label={t(`${P}.keywords`)}
                  caption={t(`${P}.optional`)}
                  help={help('keywords')}
                />
                <Input
                  id={keywordsId}
                  size="md"
                  value={form.keywords}
                  placeholder={t(`${P}.keywordsPlaceholder`)}
                  onChange={(event) => {
                    onChange({ ...form, keywords: event.target.value });
                  }}
                />
              </section>
            ) : null}
          </div>
        </div>
      </section>

      <section>
        <SectionLabel title={t(`${P}.sections.filters`)} />
        <div className="grid gap-4 border-t border-line pt-4 sm:grid-cols-2 sm:gap-6">
          <div className="min-w-0 sm:pr-6">
            {MODE_LIMITS[form.mode].map((limit, index) => (
              <SettingRow
                key={limit.key}
                first={index === 0}
                label={t(`${P}.limits.${limit.key}`)}
                help={help(limit.key)}
              >
                <NumberInput
                  value={form.limits[limit.key] ?? limit.initial}
                  min={limit.min}
                  max={limit.max}
                  label={t(`${P}.limits.${limit.key}`)}
                  onCommit={(next) => {
                    onChange({ ...form, limits: { ...form.limits, [limit.key]: next } });
                  }}
                />
              </SettingRow>
            ))}
            {counted ? (
              <SettingRow label={t(`${P}.minMessages.${form.mode}`)} help={help('minMessages')}>
                <NumberInput
                  value={form.minMessages}
                  min={1}
                  max={100}
                  label={t(`${P}.minMessages.${form.mode}`)}
                  onCommit={(minMessages) => {
                    onChange({ ...form, minMessages });
                  }}
                />
              </SettingRow>
            ) : null}
            <SettingRow label={t(`${P}.minSources`)} help={help('minSources')}>
              <NumberInput
                value={form.minSources}
                min={1}
                max={Math.max(1, sources.length)}
                label={t(`${P}.minSources`)}
                onCommit={(minSources) => {
                  onChange({ ...form, minSources });
                }}
              />
            </SettingRow>
            {SKIP_TOGGLES.map(toggle)}
            {form.mode === 'messages' ? MESSAGE_TOGGLES.map(toggle) : null}
          </div>

          <div className="min-w-0 sm:border-l sm:border-line sm:pl-6">
            <SettingRow first label={t(`${P}.lastSeen.label`)} help={help('lastSeen')}>
              <div className="w-col">
                <Select
                  value={form.lastSeen}
                  ariaLabel={t(`${P}.lastSeen.label`)}
                  options={LAST_SEEN.map((value) => ({
                    value,
                    label: t(`${P}.lastSeen.${value}`),
                  }))}
                  onChange={(value) => {
                    const lastSeen = LAST_SEEN.find((known) => known === value);
                    if (lastSeen !== undefined) onChange({ ...form, lastSeen });
                  }}
                />
              </div>
            </SettingRow>
            {PROFILE_TOGGLES.map(toggle)}
            <SettingRow label={t(`${P}.stopWords`)} htmlFor={stopId} help={help('stopWords')}>
              <div className="w-menu">
                <Input
                  id={stopId}
                  size="sm"
                  value={form.stopWords}
                  placeholder={t(`${P}.stopWordsPlaceholder`)}
                  onChange={(event) => {
                    onChange({ ...form, stopWords: event.target.value });
                  }}
                />
              </div>
            </SettingRow>
          </div>
        </div>
      </section>

      <section>
        <SectionLabel title={t(`${P}.sections.exclude`)} />
        <div className="grid gap-4 border-t border-line pt-4 sm:grid-cols-2 sm:gap-6">
          <div className="min-w-0 sm:pr-6">
            {EXCLUDE_TOGGLES.map((key, index) => (
              <SettingRow
                key={key}
                first={index === 0}
                label={t(`${P}.toggles.${key}`)}
                help={help(key)}
              >
                <Switch
                  checked={form.toggles[key]}
                  label={t(`${P}.toggles.${key}`)}
                  onChange={(on) => {
                    onChange({ ...form, toggles: { ...form.toggles, [key]: on } });
                  }}
                />
              </SettingRow>
            ))}
          </div>
          <section className="min-w-0 sm:border-l sm:border-line sm:pl-6">
            <FieldLabel
              htmlFor={blacklistId}
              label={t(`${P}.blacklist`)}
              help={help('blacklist')}
            />
            <Textarea
              id={blacklistId}
              value={form.blacklist}
              placeholder={t(`${P}.blacklistPlaceholder`)}
              onChange={(event) => {
                onChange({ ...form, blacklist: event.target.value });
              }}
            />
          </section>
        </div>
      </section>

      <section>
        <SectionLabel title={t(`${P}.sections.run`)} />
        <div className="grid gap-4 border-t border-line pt-4 sm:grid-cols-2 sm:gap-6">
          <div className="min-w-0 sm:pr-6">
            <SettingRow first label={t(`${P}.protect`)} help={help('protect')}>
              <Switch
                checked={form.protect}
                label={t(`${P}.protect`)}
                onChange={(protect) => {
                  onChange({ ...form, protect });
                }}
              />
            </SettingRow>
            <SettingRow label={t(`${P}.fast`)} help={help('fast')}>
              <Switch
                checked={form.fast}
                label={t(`${P}.fast`)}
                onChange={(fast) => {
                  onChange({ ...form, fast });
                }}
              />
            </SettingRow>
          </div>
          <div className="min-w-0 sm:border-l sm:border-line sm:pl-6">
            <SettingRow first label={t(`${P}.chatDelay`)} help={help('chatDelay')}>
              <NumberInput
                value={form.chatDelay}
                min={1}
                max={120}
                label={t(`${P}.chatDelay`)}
                onCommit={(chatDelay) => {
                  onChange({ ...form, chatDelay });
                }}
              />
            </SettingRow>
            <SettingRow label={t(`${P}.requestDelay`)} help={help('requestDelay')}>
              <NumberInput
                value={form.requestDelay}
                min={0.5}
                max={30}
                step={0.5}
                label={t(`${P}.requestDelay`)}
                onCommit={(requestDelay) => {
                  onChange({ ...form, requestDelay });
                }}
              />
            </SettingRow>
          </div>
        </div>
      </section>
    </form>
  );
}
