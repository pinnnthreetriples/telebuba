import { useQuery } from '@tanstack/react-query';
import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { discoveryAccountsQueryOptions } from '@/entities/campaign';
import { Button, Icon, Modal } from '@/shared/ui';

import { effectiveAccountIds } from '../model/filters';
import {
  applyFilters,
  EMPTY_PARSER_FORM,
  mockUsers,
  parseSources,
  topicOf,
  usersToCsv,
  type ParsedUser,
  type ParserForm,
} from '../model/userParser';
import { UserParserForm } from './UserParserForm';
import { UserParserResults, type ParserLogLine } from './UserParserResults';

const STEP_MS = 900;

function download(name: string, body: string, type: string) {
  const url = URL.createObjectURL(new Blob([body], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

type Props = {
  campaignName: string;
  campaignChannels: readonly string[];
  onClose: () => void;
};

// Та же оболочка, что у автопоиска каналов: шапка, форма, подвал с «Сбросить» / «Найти»,
// после запуска — прогон и результаты с «← Изменить параметры». Прогон — сценарий на
// таймере: бэкенда у парсера пока нет. Заготовки живут, пока открыто окно.
export function UserParserModal({ campaignName, campaignChannels, onClose }: Props) {
  const { t, i18n } = useTranslation();
  const formId = useId();
  const [form, setForm] = useState<ParserForm>(EMPTY_PARSER_FORM);
  const [presets, setPresets] = useState<{ name: string; form: ParserForm }[]>([]);
  const [submitted, setSubmitted] = useState(false);
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState(0);
  const [log, setLog] = useState<ParserLogLine[]>([]);
  const [raw, setRaw] = useState(0);
  const [users, setUsers] = useState<ParsedUser[]>([]);
  const plan = useRef<string[]>([]);
  const step = useRef(0);
  const ran = useRef<ParserForm>(EMPTY_PARSER_FORM);
  // Сколько собрано по ходу прогона — счётчик «собрано» растёт с каждым источником.
  const collected = useRef(0);

  const accounts = useQuery(discoveryAccountsQueryOptions());
  const accountList = accounts.data?.items ?? [];
  const accountIds = effectiveAccountIds(form.accountIds, accountList);
  const sources = parseSources(form.sources);
  const canRun = accountIds.length > 0 && sources.length > 0;

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      const index = step.current;
      const chat = plan.current[index];
      const at = new Date().toLocaleTimeString(i18n.language);
      const line = (tone: ParserLogLine['tone'], key: string, values = {}) => ({
        at,
        tone,
        text: t(`userParser.log.${key}`, values),
      });
      if (chat === undefined) {
        const caught = mockUsers(plan.current, collected.current);
        const kept = applyFilters(caught, ran.current);
        setRunning(false);
        setRaw(caught.length);
        setUsers(kept);
        setLog((lines) => [
          ...lines,
          line('success', 'filtered', { raw: caught.length, kept: kept.length }),
          line('success', 'done'),
        ]);
        return;
      }
      step.current = index + 1;
      setDone(index + 1);
      const topic = topicOf(chat);
      const found = 40 + ((index * 37) % 120);
      const status =
        ran.current.mode === 'members' && index === 0
          ? 'partial'
          : ran.current.mode === 'members' && index === 1
            ? 'hidden'
            : index === 1
              ? 'flood'
              : 'found';
      // Скрытый список не даёт никого; урезанный и «пауза» всё равно что-то собрали.
      const got = status === 'hidden' ? 0 : found;
      collected.current += got;
      setRaw(collected.current);
      const lines = [
        line('text', topic === null ? 'chat' : 'topic', { chat, topic }),
        // Честные статусы: что Telegram отдал не всё или не отдал вовсе — видно сразу.
        status === 'found'
          ? line('success', 'found', { count: found })
          : line('warning', status, { count: status === 'partial' ? 10000 : found }),
      ];
      setLog((prev) => [...prev, ...lines]);
    }, STEP_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [running, t, i18n.language]);

  const run = () => {
    if (!canRun) return;
    plan.current = sources;
    step.current = 0;
    ran.current = form;
    collected.current = 0;
    setDone(0);
    setRaw(0);
    setUsers([]);
    setLog([
      {
        at: new Date().toLocaleTimeString(i18n.language),
        tone: 'text',
        text: t('userParser.log.start', { accounts: accountIds.length, sources: sources.length }),
      },
    ]);
    setSubmitted(true);
    setRunning(true);
  };

  return (
    <Modal onClose={onClose} size="table" label={t('userParser.title')}>
      <div className="border-b border-line-row px-2xl pb-lg pt-xl">
        <h2 className="type-dialog-title">{t('userParser.title')}</h2>
        <p className="mt-hair type-caption">{t('userParser.sub', { name: campaignName })}</p>
      </div>

      <div className="px-2xl py-xl">
        {submitted ? (
          <UserParserResults
            mode={ran.current.mode}
            running={running}
            done={done}
            total={plan.current.length}
            raw={raw}
            accountCount={accountIds.length}
            log={log}
            users={users}
          />
        ) : (
          <UserParserForm
            form={form}
            formId={formId}
            accounts={accountList}
            accountsLoading={accounts.isPending}
            accountsErrored={accounts.isError}
            accountIds={accountIds}
            campaignChannels={campaignChannels}
            presets={presets.map((preset) => preset.name)}
            onApplyPreset={(name) => {
              const preset = presets.find((p) => p.name === name);
              if (preset !== undefined) setForm(preset.form);
            }}
            onSavePreset={() => {
              const name = t('userParser.presetName', {
                mode: t(`userParser.form.mode.${form.mode}`),
                n: presets.length + 1,
              });
              setPresets([...presets, { name, form }]);
            }}
            onChange={setForm}
            onSubmit={run}
          />
        )}
      </div>

      <div className="flex flex-wrap items-center justify-end gap-sm border-t border-line-row px-2xl py-lg">
        {submitted ? (
          <>
            <Button
              variant="ghost"
              size="sm"
              className="mr-auto"
              onClick={() => {
                setRunning(false);
                setSubmitted(false);
              }}
            >
              {t('userParser.back')}
            </Button>
            {running ? (
              <Button
                size="sm"
                onClick={() => {
                  setRunning(false);
                }}
              >
                {t('userParser.stop')}
              </Button>
            ) : (
              <>
                <Button
                  size="sm"
                  className="gap-xs"
                  disabled={users.length === 0}
                  onClick={() => {
                    download('users.csv', usersToCsv(users), 'text/csv');
                  }}
                >
                  <Icon name="download" size={14} />
                  CSV
                </Button>
                <Button
                  size="sm"
                  className="gap-xs"
                  disabled={users.length === 0}
                  onClick={() => {
                    download('users.json', JSON.stringify(users, null, 2), 'application/json');
                  }}
                >
                  <Icon name="download" size={14} />
                  JSON
                </Button>
                <Button size="sm" className="gap-xs" disabled title={t('userParser.excelLater')}>
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
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setForm(EMPTY_PARSER_FORM);
              }}
            >
              {t('userParser.reset')}
            </Button>
            <Button type="submit" form={formId} variant="primary" size="sm" disabled={!canRun}>
              {t('userParser.submit')}
            </Button>
          </>
        )}
      </div>
    </Modal>
  );
}
