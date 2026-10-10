import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AccountAvatar, accountDisplayName, BulkAccountPicker } from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { Button, ChipAddButton, HelpHint, Icon, IconButton, InlineChipEditor } from '@/shared/ui';

import type { ParserMode } from '../model/userParser';

type Help = { text: string; example?: string };

// Подпись блока: название, «?» и короткое уточнение справа.
function Heading({ title, caption, help }: { title: string; caption?: string; help: Help }) {
  return (
    <div className="mb-2 flex flex-wrap items-center gap-2">
      <span className="type-body-medium text-content-secondary">{title}</span>
      <HelpHint text={help.text} example={help.example} />
      {caption === undefined ? null : <span className="type-small">{caption}</span>}
    </div>
  );
}

/** Ссылки, вставленные пачкой: через пробел, запятую или с новой строки. */
function splitEntries(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

// Источники чипами — как «Каналы кампании»: «×» убирает, пунктирная «+ Канал» открывает
// поле ввода прямо в ряду.
export function SourceChips({
  mode,
  sources,
  campaignChannels,
  help,
  onChange,
}: {
  mode: ParserMode;
  sources: string[];
  campaignChannels: readonly string[];
  help: Help;
  onChange: (next: string[]) => void;
}) {
  const { t } = useTranslation();
  const [adding, setAdding] = useState(false);
  const [entry, setEntry] = useState('');
  const add = () => {
    onChange([...new Set([...sources, ...splitEntries(entry)])]);
    setEntry('');
  };
  const missing = campaignChannels.filter((channel) => !sources.includes(channel));

  return (
    <section className="min-w-0">
      <Heading
        title={t(`userParser.form.sources.${mode}`)}
        caption={t('userParser.form.sourcesCount', { count: sources.length })}
        help={help}
      />
      <div className="flex flex-wrap items-start gap-2">
        {sources.map((source) => (
          <span
            key={source}
            className="inline-flex h-control items-center gap-2 rounded-full border border-line bg-canvas px-3 text-body text-content-secondary"
          >
            {source}
            <IconButton
              size="sm"
              shape="circle"
              tone="danger"
              className="text-content-subtle"
              aria-label={t('userParser.form.removeSource', { source })}
              onClick={() => {
                onChange(sources.filter((other) => other !== source));
              }}
            >
              <Icon name="close" size={16} />
            </IconButton>
          </span>
        ))}
        {adding ? (
          // Поле стоит внутри <form>: Enter, которым редактор подтверждает чип, иначе ещё и
          // отправлял бы форму — то есть запускал парсинг. Гасим только отправку.
          <span
            className="contents"
            onKeyDown={(event) => {
              if (event.key === 'Enter') event.preventDefault();
            }}
          >
            <InlineChipEditor
              value={entry}
              onChange={setEntry}
              onConfirm={add}
              onCancel={() => {
                setAdding(false);
                setEntry('');
              }}
              placeholder={t(`userParser.form.sourcePlaceholder.${mode}`)}
              inputLabel={t(`userParser.form.addSource.${mode}`)}
              confirmLabel={t('userParser.form.addConfirm')}
              cancelLabel={t('userParser.form.addCancel')}
            />
          </span>
        ) : (
          <ChipAddButton
            onClick={() => {
              setAdding(true);
            }}
          >
            {t(`userParser.form.addSource.${mode}`)}
          </ChipAddButton>
        )}
      </div>
      {missing.length === 0 ? null : (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="mt-2"
          onClick={() => {
            onChange([...sources, ...missing]);
          }}
        >
          {t('userParser.form.fromCampaign', { count: missing.length })}
        </Button>
      )}
    </section>
  );
}

// Аккаунты — как в рассылке: «+» открывает общий выбор аккаунтов, выбранные стоят
// аватарками, крестик при наведении убирает одного.
export function AccountStrip({
  ids,
  fleet,
  help,
  onChange,
}: {
  ids: string[];
  fleet: readonly AccountRead[];
  help: Help;
  onChange: (ids: string[]) => void;
}) {
  const { t } = useTranslation();
  const [pickerOpen, setPickerOpen] = useState(false);
  const byId = new Map(fleet.map((account) => [account.account_id, account]));

  return (
    <section className="min-w-0">
      <Heading
        title={t('userParser.form.accounts.title')}
        caption={t('userParser.form.accounts.selected', { count: ids.length })}
        help={help}
      />
      <div className="flex items-center gap-3">
        <IconButton
          size="sm"
          aria-label={t('userParser.form.accounts.add')}
          onClick={() => {
            setPickerOpen(true);
          }}
        >
          <Icon name="plus" size={16} />
        </IconButton>
        <div className="tb-scroll flex flex-1 items-center gap-2 overflow-x-auto py-1">
          {ids.map((id) => {
            const account = byId.get(id);
            const name = account === undefined ? id : accountDisplayName(account);
            return (
              <span key={id} className="group relative shrink-0" title={name}>
                {account === undefined ? (
                  <span className="flex size-tile items-center justify-center rounded-full bg-canvas text-content-muted type-body-medium">
                    ?
                  </span>
                ) : (
                  <AccountAvatar
                    account={account}
                    className="size-tile rounded-full"
                    fallbackClassName="bg-canvas text-content-muted type-body-medium"
                  />
                )}
                <IconButton
                  size="sm"
                  shape="circle"
                  aria-label={t('userParser.form.accounts.remove', { name })}
                  onClick={() => {
                    onChange(ids.filter((other) => other !== id));
                  }}
                  className="absolute -right-1 -top-1 bg-surface-card opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
                >
                  <Icon name="close" size={16} />
                </IconButton>
              </span>
            );
          })}
        </div>
      </div>
      {pickerOpen ? (
        <BulkAccountPicker
          selected={ids}
          onApply={(next) => {
            onChange(next);
            setPickerOpen(false);
          }}
          onClose={() => {
            setPickerOpen(false);
          }}
        />
      ) : null}
    </section>
  );
}
