import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { findWarmingChannelsMutation } from '@/entities/warming';
import type { PoolFillRequest, PoolFillResult, PoolRefillRequest } from '@/shared/api';
import { Button, ConfirmModal, Input, Modal, ModalFooter, ModalHeader, Notice } from '@/shared/ui';

// «Найти каналы» (Прогрев 2.0, 4.5 and its follow-up). A search with an account outside
// warming; everything it finds that passes the filters joins the pool at once, and the
// operator removes what they do not want from the list above. When no account is free
// the server names the busy one it would take and nothing happens until the operator
// agrees in the confirm dialog («Выдернуть на поиск»). The automatic refill asks the
// same way, through the banner, when it found no free account on its own.
export function ChannelPoolFill({
  defaultLanguage,
  refillRequest,
  onPoolChanged,
}: {
  defaultLanguage?: string;
  refillRequest?: PoolRefillRequest | null;
  onPoolChanged: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [language, setLanguage] = useState(defaultLanguage ?? 'es');
  const [country, setCountry] = useState('');
  const [topics, setTopics] = useState('');
  const [result, setResult] = useState<PoolFillResult | null>(null);
  const [consent, setConsent] = useState<PoolFillRequest | null>(null);
  const find = useMutation(findWarmingChannelsMutation());
  const topicList = topics
    .split(',')
    .map((topic) => topic.trim())
    .filter((topic) => topic.length >= 2)
    .slice(0, 5);

  const run = (body: PoolFillRequest) => {
    find.mutate(
      { body },
      {
        onSuccess: (found) => {
          setResult(found);
          if (found.status === 'needs_consent' && found.pull_candidate) {
            setConsent({ ...body, pull_account_id: found.pull_candidate.account_id });
          }
          if ((found.added ?? 0) > 0) onPoolChanged();
        },
      },
    );
  };
  const runForm = () => {
    run({
      language: language.trim().toLowerCase(),
      country: country.trim() ? country.trim().toUpperCase() : null,
      topics: topicList,
    });
  };
  const answerRefill = (request: PoolRefillRequest) => {
    setLanguage(request.language);
    setOpen(true);
    run({ language: request.language, topics: [] });
  };
  const candidate = result?.pull_candidate;

  return (
    <div className="mt-4">
      {refillRequest ? (
        <div className="mb-3">
          <Notice tone="warning">
            <p className="m-0 type-small-medium">{t('warming.poolFill.refillTitle')}</p>
            <p className="m-0 mt-1 type-small">
              {t('warming.poolFill.refillHint', { language: refillRequest.language })}
            </p>
            <div className="mt-2">
              <Button
                size="sm"
                loading={find.isPending}
                onClick={() => {
                  answerRefill(refillRequest);
                }}
              >
                {t('warming.poolFill.refillAction')}
              </Button>
            </div>
          </Notice>
        </div>
      ) : null}
      <Button
        size="sm"
        onClick={() => {
          setOpen(true);
        }}
      >
        {t('warming.poolFill.open')}
      </Button>

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
                    {t(`warming.poolFill.status.${result.status}`, {
                      found: result.found ?? 0,
                      added: result.added ?? 0,
                    })}
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
                  disabled={language.trim().length !== 2}
                  loading={find.isPending}
                  onClick={runForm}
                >
                  {t('warming.poolFill.search')}
                </Button>
              </ModalFooter>
            </>
          )}
        </Modal>
      ) : null}

      {consent && candidate ? (
        <ConfirmModal
          title={t('warming.poolFill.consentTitle')}
          body={t('warming.poolFill.consentBody', {
            name: candidate.name,
            work: t(`warming.poolFill.busyWith.${candidate.busy_with}`),
          })}
          confirmLabel={t('warming.poolFill.consentConfirm')}
          cancelLabel={t('warming.poolFill.consentCancel')}
          onClose={() => {
            setConsent(null);
          }}
          onConfirm={() => {
            run(consent);
          }}
        />
      ) : null}
    </div>
  );
}
