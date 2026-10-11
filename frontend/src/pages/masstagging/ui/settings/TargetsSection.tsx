// Section 3: who to tag. Three sources: the operator's own list (typed, pasted or read
// from a file), the contacts of the chosen accounts, or a saved parser base — copied into
// the list when picked, never linked. The list shows its first chips and "ещё N".
import { useMutation, useQuery } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  copyMasstaggingBaseMutation,
  importMasstaggingTargetsMutation,
} from '@/entities/masstagging';
import { userParserBasesQueryOptions } from '@/entities/user-parser';
import {
  Badge,
  Button,
  Icon,
  IconButton,
  SegmentedControl,
  Select,
  Spinner,
  Textarea,
} from '@/shared/ui';

import { mergeTargets, parseTargets, type Settings } from '../../model/draft';

import { Section } from './fields';

export const CHIPS_SHOWN = 8;
const SOURCES = ['manual', 'contacts', 'base'] as const;

type Patch = (next: Partial<Settings>) => void;

function Chips({ targets, onPatch }: { targets: string[]; onPatch: Patch }) {
  const { t } = useTranslation();
  const [all, setAll] = useState(false);
  if (targets.length === 0) return null;
  const shown = all ? targets : targets.slice(0, CHIPS_SHOWN);
  return (
    <div className="flex flex-wrap items-center gap-2">
      {shown.map((target) => (
        <span
          key={target}
          className="inline-flex items-center gap-1 rounded-full border border-line bg-canvas py-1 pl-3 pr-1 text-body"
        >
          {target}
          <IconButton
            size="sm"
            shape="circle"
            aria-label={t('masstagging.settings.targets.remove', { target })}
            onClick={() => {
              onPatch({ targets: targets.filter((other) => other !== target) });
            }}
          >
            <Icon name="close" size={14} />
          </IconButton>
        </span>
      ))}
      {targets.length > CHIPS_SHOWN ? (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setAll((on) => !on);
          }}
        >
          {all
            ? t('masstagging.settings.targets.collapse')
            : t('masstagging.settings.targets.more', { count: targets.length - CHIPS_SHOWN })}
        </Button>
      ) : null}
    </div>
  );
}

function ManualEntry({
  campaignId,
  settings,
  onPatch,
}: {
  campaignId: string;
  settings: Settings;
  onPatch: Patch;
}) {
  const { t } = useTranslation();
  const [entry, setEntry] = useState('');
  const [skipped, setSkipped] = useState(0);
  const fileRef = useRef<HTMLInputElement>(null);
  const importFile = useMutation(importMasstaggingTargetsMutation());
  const parsed = parseTargets(entry, settings.targets);
  const add = () => {
    if (parsed.added.length > 0) onPatch({ targets: [...settings.targets, ...parsed.added] });
    setSkipped(parsed.invalid);
    setEntry('');
  };
  return (
    <>
      <Textarea
        value={entry}
        aria-label={t('masstagging.settings.targets.entry')}
        placeholder={t('masstagging.settings.targets.placeholder')}
        onChange={(event) => {
          setEntry(event.target.value);
        }}
      />
      <input
        ref={fileRef}
        type="file"
        accept=".txt,.csv,text/plain,text/csv"
        className="hidden"
        aria-label={t('masstagging.settings.targets.file')}
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file === undefined) return;
          void importFile
            .mutateAsync({ path: { campaign_id: campaignId }, body: { file } })
            .then((found) => {
              onPatch({ targets: mergeTargets(settings.targets, found.targets) });
              setSkipped(found.skipped ?? 0);
            })
            .catch(() => undefined);
        }}
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="primary" disabled={parsed.added.length === 0} onClick={add}>
          <Icon name="plus" size={14} />
          {parsed.added.length > 0
            ? t('masstagging.settings.targets.addCount', { count: parsed.added.length })
            : t('masstagging.settings.targets.add')}
        </Button>
        <Button size="sm" loading={importFile.isPending} onClick={() => fileRef.current?.click()}>
          {importFile.isPending ? null : <Icon name="upload-cloud" size={14} />}
          {t('masstagging.settings.targets.file')}
        </Button>
        {skipped > 0 ? (
          <span className="type-small">
            {t('masstagging.settings.targets.skipped', { count: skipped })}
          </span>
        ) : null}
        {settings.targets.length > 0 ? (
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto"
            onClick={() => {
              onPatch({ targets: [], base_name: '' });
            }}
          >
            {t('masstagging.settings.targets.clear')}
          </Button>
        ) : null}
      </div>
    </>
  );
}

function BasePicker({
  campaignId,
  settings,
  onPatch,
}: {
  campaignId: string;
  settings: Settings;
  onPatch: Patch;
}) {
  const { t } = useTranslation();
  const bases = useQuery(userParserBasesQueryOptions());
  const copy = useMutation(copyMasstaggingBaseMutation());
  const items = (bases.data?.items ?? []).filter((base) => base.status !== 'running');
  if (bases.data !== undefined && items.length === 0) {
    return (
      <span className="type-body text-content-subtle">
        {t('masstagging.settings.targets.noBases')}
      </span>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        value=""
        ariaLabel={t('masstagging.settings.targets.base')}
        placeholder={t('masstagging.settings.targets.basePlaceholder')}
        options={items.map((base) => ({
          value: base.run_id,
          label: t('masstagging.settings.targets.baseOption', {
            name: base.name,
            count: base.kept,
          }),
        }))}
        onChange={(baseId) => {
          void copy
            .mutateAsync({ path: { campaign_id: campaignId }, body: { base_id: baseId } })
            .then((found) => {
              onPatch({
                targets: mergeTargets(settings.targets, found.targets),
                base_name: found.name ?? '',
              });
            })
            .catch(() => undefined);
        }}
      />
      {copy.isPending ? <Spinner /> : null}
      {settings.base_name ? (
        <span className="type-small">
          {t('masstagging.settings.targets.copiedFrom', { name: settings.base_name })}
        </span>
      ) : null}
    </div>
  );
}

export function TargetsSection({
  campaignId,
  settings,
  onPatch,
}: {
  campaignId: string;
  settings: Settings;
  onPatch: Patch;
}) {
  const { t } = useTranslation();
  const source = settings.target_source;
  return (
    <Section
      n={3}
      title={t('masstagging.settings.targets.title')}
      hint={t('masstagging.settings.targets.hint')}
      example={t('masstagging.settings.targets.example')}
      aside={
        source !== 'contacts' && settings.targets.length > 0 ? (
          <Badge>{settings.targets.length}</Badge>
        ) : undefined
      }
    >
      <SegmentedControl
        variant="tray"
        value={source}
        ariaLabel={t('masstagging.settings.targets.source')}
        options={SOURCES.map((value) => ({
          value,
          label: t(`masstagging.settings.targets.sources.${value}`),
        }))}
        onChange={(targetSource) => {
          onPatch({ target_source: targetSource });
        }}
      />
      {source === 'contacts' ? (
        <span className="type-body text-content-subtle">
          {t('masstagging.settings.targets.contacts')}
        </span>
      ) : (
        <>
          {source === 'manual' ? (
            <ManualEntry campaignId={campaignId} settings={settings} onPatch={onPatch} />
          ) : (
            <BasePicker campaignId={campaignId} settings={settings} onPatch={onPatch} />
          )}
          <Chips targets={settings.targets} onPatch={onPatch} />
        </>
      )}
    </Section>
  );
}
