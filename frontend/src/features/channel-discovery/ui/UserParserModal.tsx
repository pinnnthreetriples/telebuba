import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { allAccountsQueryOptions } from '@/entities/account';
import {
  createUserParserPresetMutation,
  startUserParserRunMutation,
  stopUserParserRunMutation,
  userParserAccountsQueryOptions,
  userParserBasesQueryOptions,
  userParserPresetsQueryOptions,
  userParserRunQueryOptions,
} from '@/entities/user-parser';
import { useLogEventStream } from '@/shared/lib';
import {
  Button,
  HelpHint,
  Icon,
  Modal,
  ModalFooter,
  ModalHeader,
  SegmentedControl,
  Select,
} from '@/shared/ui';

import { effectiveAccountIds } from '../model/filters';
import { useUserPages } from '../model/useUserPages';
import {
  EMPTY_PARSER_FORM,
  formToRequest,
  formToSettings,
  parseSources,
  settingsToForm,
  type ParserForm,
} from '../model/userParser';
import { downloadExport } from '../model/userParserExport';
import { runLog } from '../model/userParserLog';
import { UserParserBases } from './UserParserBases';
import { UserParserForm } from './UserParserForm';
import { UserParserResults } from './UserParserResults';

const ACCOUNTS_POLL_MS = 15_000;
// Страховка на случай пропущенного SSE: пока прогон идёт, окно спрашивает его и само.
const RUN_POLL_MS = 3_000;
const REFUSED = 'userParser.refused';

type Props = {
  campaignName: string;
  campaignChannels: readonly string[];
  onClose: () => void;
};

// Та же оболочка, что у автопоиска каналов: шапка, форма, подвал с «Сбросить» / «Найти»,
// после запуска — прогон и результаты с «← Изменить параметры». Прогон идёт на сервере:
// окно запускает его, следит за ним (опрос + SSE) и показывает собранных постранично.
export function UserParserModal({ campaignName, campaignChannels, onClose }: Props) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const formId = useId();
  const [form, setForm] = useState<ParserForm>(EMPTY_PARSER_FORM);
  const [tab, setTab] = useState<'new' | 'bases'>('new');
  const [runId, setRunId] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  const accounts = useQuery({
    ...userParserAccountsQueryOptions(),
    refetchInterval: submitted ? false : ACCOUNTS_POLL_MS,
  });
  const accountList = accounts.data?.items ?? [];
  // Пока оператор не трогал выбор — свободные аккаунты, Premium первыми (как в автопоиске).
  const accountIds = form.accountIds ?? effectiveAccountIds(null, accountList);
  const busy = new Map(
    accountList.flatMap((account) =>
      account.busy_reason == null
        ? []
        : [
            [
              account.account_id,
              t(`neurocomment.modal.discovery.form.accounts.busy.${account.busy_reason}`),
            ],
          ],
    ),
  );
  const fleet = useQuery(allAccountsQueryOptions());
  const sources = parseSources(form.sources);
  const canRun = accountIds.length > 0 && sources.length > 0;

  const basesOptions = userParserBasesQueryOptions();
  const bases = useQuery(basesOptions);
  const presetsOptions = userParserPresetsQueryOptions();
  const presets = useQuery(presetsOptions).data?.items ?? [];
  const savePreset = useMutation({
    ...createUserParserPresetMutation(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: presetsOptions.queryKey }),
  });

  const runOptions = userParserRunQueryOptions({ path: { run_id: runId ?? '' } });
  const run = useQuery({
    ...runOptions,
    enabled: runId !== null,
    refetchInterval: (query) => (query.state.data?.status === 'running' ? RUN_POLL_MS : false),
  });
  const settled = run.data !== undefined && run.data.status !== 'running';
  const people = useUserPages(runId, '', settled);
  const start = useMutation(startUserParserRunMutation());
  const stop = useMutation(stopUserParserRunMutation());
  const running = submitted && !settled;

  // Общий поток SSE приходит на всё приложение; наши — только события парсера.
  useLogEventStream((entry) => {
    if (!entry.event.startsWith('user_parser')) return;
    void queryClient.invalidateQueries({ queryKey: runOptions.queryKey });
    void queryClient.invalidateQueries({ queryKey: basesOptions.queryKey });
  });

  const refusal = start.data?.status !== undefined && start.data.status !== 'started';
  const refusedId = start.data?.refused_account_id;
  const refusedName =
    refusedId == null
      ? null
      : (accountList.find((account) => account.account_id === refusedId)?.name ?? refusedId);

  const launch = () => {
    if (!canRun) return;
    const name = t('userParser.bases.name', {
      mode: t(`userParser.bases.modeName.${form.mode}`),
      date: new Date().toLocaleDateString(i18n.language),
    });
    start.mutate(
      { body: formToRequest(form, accountIds, name) },
      {
        onSuccess: (outcome) => {
          if (outcome.status !== 'started' || outcome.run_id == null) return;
          setRunId(outcome.run_id);
          setSubmitted(true);
        },
      },
    );
  };

  const presetName = () => {
    const taken = new Set(presets.map((preset) => preset.name.toLowerCase()));
    const mode = t(`userParser.form.mode.${form.mode}`);
    let n = presets.length + 1;
    while (taken.has(t('userParser.presetName', { mode, n }).toLowerCase())) n += 1;
    return t('userParser.presetName', { mode, n });
  };

  const current = run.data;
  const log = current === undefined ? [] : runLog(current, t, i18n.language);

  return (
    <Modal onClose={onClose} size="table" label={t('userParser.title')}>
      <ModalHeader
        title={t('userParser.title')}
        subtitle={t('userParser.sub', { name: campaignName })}
      >
        <div className="ml-auto">
          <SegmentedControl
            variant="pill"
            value={tab}
            ariaLabel={t('userParser.tabs.label')}
            options={[
              { value: 'new', label: t('userParser.tabs.new') },
              {
                value: 'bases',
                label: t('userParser.tabs.bases', { count: bases.data?.items.length ?? 0 }),
              },
            ]}
            onChange={setTab}
          />
        </div>
      </ModalHeader>

      <div className="px-6 py-6">
        {tab === 'bases' ? (
          <UserParserBases />
        ) : submitted ? (
          <UserParserResults
            mode={current?.mode ?? form.mode}
            running={running}
            done={current?.sources_done ?? 0}
            total={current?.sources_total ?? sources.length}
            raw={current?.collected_raw ?? 0}
            kept={settled ? (current?.kept ?? 0) : 0}
            accountCount={current?.accounts?.length ?? accountIds.length}
            log={log}
            users={people.users}
            hasMore={people.hasMore}
            loadingMore={people.loadingMore}
            onMore={people.more}
          />
        ) : (
          <div className="flex flex-col gap-4">
            {refusal ? (
              <p role="status" className="type-body text-danger">
                {t(`${REFUSED}.${start.data?.status ?? 'account_busy'}`)}
                {refusedName === null ? '' : ` — ${t(`${REFUSED}.account`, { name: refusedName })}`}
              </p>
            ) : null}
            <UserParserForm
              form={form}
              formId={formId}
              fleet={fleet.data?.items ?? []}
              accountIds={accountIds}
              busy={busy}
              campaignChannels={campaignChannels}
              onChange={setForm}
              onSubmit={launch}
            />
          </div>
        )}
      </div>

      <ModalFooter>
        {tab === 'bases' ? (
          <Button variant="primary" size="sm" onClick={onClose}>
            {t('userParser.close')}
          </Button>
        ) : submitted ? (
          <>
            <Button
              variant="ghost"
              size="sm"
              className="mr-auto"
              onClick={() => {
                setSubmitted(false);
              }}
            >
              {t('userParser.back')}
            </Button>
            {running ? (
              <Button
                size="sm"
                disabled={stop.isPending || runId === null}
                onClick={() => {
                  if (runId === null) return;
                  stop.mutate(
                    { path: { run_id: runId } },
                    {
                      onSuccess: (stopped) => {
                        queryClient.setQueryData(runOptions.queryKey, stopped);
                      },
                    },
                  );
                }}
              >
                {t('userParser.stop')}
              </Button>
            ) : (
              <>
                {(['csv', 'json'] as const).map((format) => (
                  <Button
                    key={format}
                    size="sm"
                    className="gap-1"
                    disabled={runId === null || (current?.kept ?? 0) === 0}
                    onClick={() => {
                      if (runId !== null) downloadExport(runId, format);
                    }}
                  >
                    <Icon name="download" size={14} />
                    {format.toUpperCase()}
                  </Button>
                ))}
                <Button size="sm" className="gap-1" disabled title={t('userParser.excelLater')}>
                  <Icon name="download" size={14} />
                  Excel
                </Button>
                <Button variant="primary" size="sm" onClick={onClose}>
                  {t('userParser.close')}
                </Button>
              </>
            )}
          </>
        ) : (
          <>
            {/* Заготовки — в подвале слева: это не настройка парсинга, а действие над ней. */}
            <div className="mr-auto flex items-center gap-2">
              <div className="w-menu">
                <Select
                  value=""
                  placeholder={t('userParser.form.presets.label')}
                  emptyLabel={t('userParser.form.presets.empty')}
                  ariaLabel={t('userParser.form.presets.label')}
                  options={presets.map((preset) => ({
                    value: preset.preset_id,
                    label: preset.name,
                  }))}
                  onChange={(presetId) => {
                    const preset = presets.find((p) => p.preset_id === presetId);
                    if (preset !== undefined) setForm(settingsToForm(preset.settings));
                  }}
                />
              </div>
              <Button
                size="sm"
                disabled={savePreset.isPending}
                onClick={() => {
                  savePreset.mutate({
                    body: { name: presetName(), settings: formToSettings(form) },
                  });
                }}
              >
                {t('userParser.form.presets.save')}
              </Button>
              <HelpHint
                text={t('userParser.help.presets.text')}
                example={t('userParser.help.presets.ex')}
              />
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setForm(EMPTY_PARSER_FORM);
              }}
            >
              {t('userParser.reset')}
            </Button>
            <Button
              type="submit"
              form={formId}
              variant="primary"
              size="sm"
              disabled={!canRun || start.isPending}
            >
              {t('userParser.submit')}
            </Button>
          </>
        )}
      </ModalFooter>
    </Modal>
  );
}
