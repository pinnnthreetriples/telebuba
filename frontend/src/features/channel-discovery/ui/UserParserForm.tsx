import { useId, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import type { AccountRead } from '@/shared/api';
import {
  Icon,
  Input,
  SectionLabel,
  SegmentedControl,
  Select,
  SettingRow,
  Switch,
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
import { AccountStrip, SourceChips } from './UserParserPickers';

const P = 'userParser.form';
const H = 'userParser.help';

type Help = { text: string; example?: string };

// Число, которое можно стереть, не получив ноль до потери фокуса. `text` + `inputMode`,
// а не `type="number"`: стрелки числового поля съедали ширину, и «1000» читалось «100(».
function NumberInput({
  id,
  value,
  min,
  max,
  label,
  onCommit,
}: {
  id?: string;
  value: number;
  min: number;
  max: number;
  label?: string;
  onCommit: (next: number) => void;
}) {
  const [text, setText] = useState<string | null>(null);
  return (
    <Input
      id={id}
      size="sm"
      type="text"
      inputMode="decimal"
      className="w-number tabular-nums"
      aria-label={label}
      value={text ?? String(value)}
      onChange={(event) => {
        const raw = event.target.value.replace(',', '.');
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

// Блок формы: подпись и содержимое под волосяной линией — как фильтры автопоиска.
function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <SectionLabel title={title} />
      <div className="border-t border-line pt-3">{children}</div>
    </section>
  );
}

type Props = {
  form: ParserForm;
  formId: string;
  fleet: readonly AccountRead[];
  // Выбранные аккаунты: свои, если оператор их трогал, иначе — выбор по умолчанию.
  accountIds: string[];
  // Занятые аккаунты: id -> чем занят. Они остаются в ряду, но приглушены и подписаны.
  busy: ReadonlyMap<string, string>;
  // Каналы кампании, из которой открыт парсер: их можно подставить в источники одной кнопкой.
  campaignChannels: readonly string[];
  onChange: (form: ParserForm) => void;
  onSubmit: () => void;
};

// Порядок — порядок решений: что собираем → кем и откуда → сколько смотреть → кого
// оставить → кого выкинуть. Скорость и защита нужны редко и свёрнуты.
export function UserParserForm({
  form,
  formId,
  fleet,
  accountIds,
  busy,
  campaignChannels,
  onChange,
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  const keywordsId = useId();
  const stopId = useId();
  const blacklistId = useId();
  const [speedOpen, setSpeedOpen] = useState(false);
  const sources = parseSources(form.sources);
  const counted = form.mode !== 'members';
  // Подсказка: фраза и пример. Пример необязателен — пустой не рисуется.
  const help = (key: string): Help => {
    const example = t(`${H}.${key}.ex`, { defaultValue: '' });
    return { text: t(`${H}.${key}.text`), example: example === '' ? undefined : example };
  };
  const setToggle = (key: ParserToggle, on: boolean) => {
    onChange({ ...form, toggles: { ...form.toggles, [key]: on } });
  };
  const toggleRow = (key: ParserToggle, first = false) => (
    <SettingRow key={key} first={first} label={t(`${P}.toggles.${key}`)} help={help(key)}>
      <Switch
        checked={form.toggles[key]}
        label={t(`${P}.toggles.${key}`)}
        onChange={(on) => {
          setToggle(key, on);
        }}
      />
    </SettingRow>
  );
  // Лимиты режима и — в режиме сообщений — что считать сообщением, поровну в две колонки.
  const scopeRows = [
    ...MODE_LIMITS[form.mode].map((limit, index) => (
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
    )),
    ...(form.mode === 'messages' ? MESSAGE_TOGGLES : []).map((key, index) =>
      toggleRow(key, index === 0),
    ),
  ];
  const scopeHalf = Math.ceil(scopeRows.length / 2);
  const exclusions = [...SKIP_TOGGLES, ...EXCLUDE_TOGGLES];
  const half = Math.ceil(exclusions.length / 2);

  return (
    <form
      id={formId}
      className="flex flex-col gap-6"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      {/* Главный выбор — первым и во всю ширину: от него зависят подписи и поля ниже. */}
      <section className="flex flex-col gap-2">
        <SegmentedControl
          value={form.mode}
          ariaLabel={t(`${P}.mode.label`)}
          options={PARSER_MODES.map((mode) => ({ value: mode, label: t(`${P}.mode.${mode}`) }))}
          onChange={(mode) => {
            onChange({ ...form, mode });
          }}
        />
        <p className="type-small">{t(`${H}.mode.${form.mode}.text`)}</p>
      </section>

      <div className="grid gap-4 sm:grid-cols-2 sm:gap-6">
        <AccountStrip
          ids={accountIds}
          fleet={fleet}
          busy={busy}
          help={help('accounts')}
          onChange={(ids) => {
            onChange({ ...form, accountIds: ids });
          }}
        />
        <SourceChips
          mode={form.mode}
          sources={sources}
          campaignChannels={campaignChannels}
          help={help('sources')}
          onChange={(next) => {
            onChange({ ...form, sources: next.join('\n') });
          }}
        />
      </div>

      <Block title={t(`${P}.sections.scope`)}>
        {/* Тот же вид, что у остальных блоков: подпись слева, контрол справа, две колонки. */}
        <div className="grid gap-x-6 sm:grid-cols-2">
          <div className="min-w-0">{scopeRows.slice(0, scopeHalf)}</div>
          <div className="min-w-0 border-t border-canvas sm:border-t-0">
            {scopeRows.slice(scopeHalf)}
          </div>
        </div>
        {counted ? (
          <SettingRow label={t(`${P}.keywords`)} htmlFor={keywordsId} help={help('keywords')}>
            <div className="w-full sm:w-auto sm:flex-1">
              <Input
                id={keywordsId}
                size="sm"
                value={form.keywords}
                placeholder={t(`${P}.keywordsPlaceholder`)}
                onChange={(event) => {
                  onChange({ ...form, keywords: event.target.value });
                }}
              />
            </div>
          </SettingRow>
        ) : null}
      </Block>

      <Block title={t(`${P}.sections.quality`)}>
        <div className="grid gap-x-6 sm:grid-cols-2">
          <div className="min-w-0">
            {counted ? (
              <SettingRow
                first
                label={t(`${P}.minMessages.${form.mode}`)}
                help={help('minMessages')}
              >
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
            <SettingRow first={!counted} label={t(`${P}.minSources`)} help={help('minSources')}>
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
            <SettingRow label={t(`${P}.lastSeen.label`)} help={help('lastSeen')}>
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
            <SettingRow label={t(`${P}.stopWords`)} htmlFor={stopId} help={help('stopWords')}>
              <div className="w-col">
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
          <div className="min-w-0 border-t border-canvas sm:border-t-0">
            {PROFILE_TOGGLES.map((key, index) => toggleRow(key, index === 0))}
          </div>
        </div>
      </Block>

      <Block title={t(`${P}.sections.exclude`)}>
        <div className="grid gap-x-6 sm:grid-cols-2">
          <div className="min-w-0">
            {exclusions.slice(0, half).map((key, index) => toggleRow(key, index === 0))}
          </div>
          <div className="min-w-0 border-t border-canvas sm:border-t-0">
            {exclusions.slice(half).map((key, index) => toggleRow(key, index === 0))}
          </div>
        </div>
        <SettingRow label={t(`${P}.blacklist`)} htmlFor={blacklistId} help={help('blacklist')}>
          <div className="w-full sm:w-auto sm:flex-1">
            <Input
              id={blacklistId}
              size="sm"
              value={form.blacklist}
              placeholder={t(`${P}.blacklistPlaceholder`)}
              onChange={(event) => {
                onChange({ ...form, blacklist: event.target.value });
              }}
            />
          </div>
        </SettingRow>
      </Block>

      {/* Редкие настройки: значения по умолчанию подходят почти всегда, поэтому свёрнуты.
          Заголовок — тот же, что у остальных блоков, а не карточный. */}
      <section>
        <button
          type="button"
          aria-expanded={speedOpen}
          onClick={() => {
            setSpeedOpen(!speedOpen);
          }}
          className="flex w-full items-center justify-between gap-2 border-t border-line pt-3 text-left"
        >
          <span className="type-small-medium">{t(`${P}.sections.speed`)}</span>
          <span className="flex items-center gap-1 type-small">
            {speedOpen
              ? null
              : t(`${P}.speedSummary.${form.protect ? 'on' : 'off'}`, { chat: form.chatDelay })}
            <Icon name={speedOpen ? 'chevron-down' : 'chevron-right'} size={14} />
          </span>
        </button>
        {speedOpen ? (
          <div className="pt-3">
            <div className="grid gap-x-6 sm:grid-cols-2">
              <div className="min-w-0">
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
              <div className="min-w-0 border-t border-canvas sm:border-t-0">
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
                    label={t(`${P}.requestDelay`)}
                    onCommit={(requestDelay) => {
                      onChange({ ...form, requestDelay });
                    }}
                  />
                </SettingRow>
              </div>
            </div>
          </div>
        ) : null}
      </section>
    </form>
  );
}
