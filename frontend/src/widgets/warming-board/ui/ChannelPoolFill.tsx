import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  approveWarmingChannelProposalsMutation,
  findWarmingChannelsMutation,
  rejectWarmingChannelProposalsMutation,
  warmingChannelProposalsQueryKey,
  warmingChannelProposalsQueryOptions,
} from '@/entities/warming';
import type { PoolFillResult } from '@/shared/api';
import { Button, Input, Modal, ModalFooter, ModalHeader } from '@/shared/ui';

// «Найти каналы» (Прогрев 2.0, item 4.5). A search proposes channels in a language,
// country and topics; the operator approves the ones to warm on, in a batch. Nothing
// a search finds is used before that (decision Q12: manual approval only).
export function ChannelPoolFill({
  defaultLanguage,
  onPoolChanged,
}: {
  defaultLanguage?: string;
  onPoolChanged: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [language, setLanguage] = useState(defaultLanguage ?? 'es');
  const [country, setCountry] = useState('');
  const [topics, setTopics] = useState('');
  const [result, setResult] = useState<PoolFillResult | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const proposals = useQuery(warmingChannelProposalsQueryOptions());
  const find = useMutation(findWarmingChannelsMutation());
  const approve = useMutation(approveWarmingChannelProposalsMutation());
  const reject = useMutation(rejectWarmingChannelProposalsMutation());
  const waiting = proposals.data?.channels ?? [];
  const topicList = topics
    .split(',')
    .map((topic) => topic.trim())
    .filter((topic) => topic.length >= 2)
    .slice(0, 5);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: warmingChannelProposalsQueryKey() });
  };
  const runSearch = () => {
    find.mutate(
      {
        body: {
          language: language.trim().toLowerCase(),
          country: country.trim() ? country.trim().toUpperCase() : null,
          topics: topicList,
        },
      },
      {
        onSuccess: (found) => {
          setResult(found);
          refresh();
        },
      },
    );
  };
  const decide = (keep: boolean) => {
    const body = { channels: [...picked] };
    const mutation = keep ? approve : reject;
    mutation.mutate(
      { body },
      {
        onSuccess: () => {
          setPicked(new Set());
          refresh();
          onPoolChanged();
        },
      },
    );
  };
  const toggle = (channel: string) => {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(channel)) next.delete(channel);
      else next.add(channel);
      return next;
    });
  };

  return (
    <div className="mt-4">
      <Button
        size="sm"
        onClick={() => {
          setOpen(true);
        }}
      >
        {t('warming.poolFill.open')}
      </Button>

      {waiting.length > 0 ? (
        <div className="mt-3">
          <p className="m-0 mb-2 type-small-medium text-content-primary">
            {t('warming.poolFill.waiting', { count: waiting.length })}
          </p>
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {waiting.map((channel) => (
              <li key={channel.channel} className="flex items-center gap-2 type-small">
                <input
                  type="checkbox"
                  checked={picked.has(channel.channel)}
                  onChange={() => {
                    toggle(channel.channel);
                  }}
                  aria-label={t('warming.poolFill.pick', { channel: channel.channel })}
                />
                <span className="min-w-0 flex-1 truncate">
                  @{channel.channel}
                  {channel.label ? ` · ${channel.label}` : ''}
                </span>
                {channel.subscribers != null ? (
                  <span className="text-content-subtle">
                    {t('warming.poolFill.subscribers', { count: channel.subscribers })}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
          <div className="mt-2 flex gap-2">
            <Button
              size="sm"
              variant="primary"
              disabled={picked.size === 0}
              loading={approve.isPending}
              onClick={() => {
                decide(true);
              }}
            >
              {t('warming.poolFill.approve')}
            </Button>
            <Button
              size="sm"
              disabled={picked.size === 0}
              loading={reject.isPending}
              onClick={() => {
                decide(false);
              }}
            >
              {t('warming.poolFill.reject')}
            </Button>
          </div>
        </div>
      ) : null}

      {open ? (
        <Modal
          onClose={() => {
            setOpen(false);
            setResult(null);
          }}
          size="confirm"
          label={t('warming.poolFill.title')}
        >
          {(close) => (
            <>
              <ModalHeader
                title={t('warming.poolFill.title')}
                subtitle={t('warming.poolFill.hint')}
              />
              <div className="flex flex-col gap-3 px-6 py-4">
                <label className="flex flex-col gap-1">
                  <span className="type-small-medium">{t('warming.poolFill.language')}</span>
                  <Input
                    value={language}
                    maxLength={2}
                    onChange={(event) => {
                      setLanguage(event.target.value);
                    }}
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="type-small-medium">{t('warming.poolFill.country')}</span>
                  <Input
                    value={country}
                    maxLength={2}
                    onChange={(event) => {
                      setCountry(event.target.value);
                    }}
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="type-small-medium">{t('warming.poolFill.topics')}</span>
                  <Input
                    value={topics}
                    onChange={(event) => {
                      setTopics(event.target.value);
                    }}
                  />
                </label>
                {result ? (
                  <p className="m-0 type-small text-content-muted" role="status">
                    {result.status === 'ok'
                      ? t('warming.poolFill.result', {
                          found: result.found ?? 0,
                          proposed: result.proposed ?? 0,
                        })
                      : t('warming.poolFill.noAccount')}
                  </p>
                ) : null}
                {find.isError ? (
                  <p className="m-0 type-small text-danger-deep">{t('warming.poolFill.error')}</p>
                ) : null}
              </div>
              <ModalFooter>
                <Button onClick={close}>{t('warming.poolFill.close')}</Button>
                <Button
                  variant="primary"
                  disabled={topicList.length === 0 || language.trim().length !== 2}
                  loading={find.isPending}
                  onClick={runSearch}
                >
                  {t('warming.poolFill.search')}
                </Button>
              </ModalFooter>
            </>
          )}
        </Modal>
      ) : null}
    </div>
  );
}
