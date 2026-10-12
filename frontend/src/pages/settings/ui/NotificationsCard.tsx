import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  notificationSettingsQueryOptions,
  sendTestNotificationMutation,
  updateNotificationSettingsMutation,
} from '@/entities/notification';
import type { NotificationSettings } from '@/shared/api';
import { mutationErrorText } from '@/shared/lib';
import { Button, Card, Input, Notice, SettingRow, Switch } from '@/shared/ui';

import { ApiKeyField } from './ApiKeyField';

const FIELD_LABEL = 'mb-2 block type-body-medium text-content-secondary';
// The backend's rule (`schemas/notifications.py`): a numeric id or a public @channel.
const CHAT_ID = /^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{3,31})$/;

type Feedback = { tone: 'success' | 'danger'; text: string } | null;

// The operator bot: its own card and its own save, because it is a separate settings
// row — saving it must never send the LLM form's fields, nor the other way round.
// The token behaves like an LLM key: typed replaces, blank keeps, "clear" wipes.
function NotificationsForm({ settings }: { settings: NotificationSettings }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const save = useMutation({
    ...updateNotificationSettingsMutation(),
    meta: { inlineError: true },
  });
  const sendTest = useMutation({ ...sendTestNotificationMutation(), meta: { inlineError: true } });
  const chatInputId = useId();
  const chatHintId = useId();

  const [enabled, setEnabled] = useState(settings.enabled);
  const [token, setToken] = useState('');
  const [showToken, setShowToken] = useState(false);
  const [clearToken, setClearToken] = useState(false);
  const [chatId, setChatId] = useState(settings.chat_id);
  const [feedback, setFeedback] = useState<Feedback>(null);

  const trimmedChat = chatId.trim();
  const chatInvalid = trimmedChat !== '' && !CHAT_ID.test(trimmedChat);
  const dirty =
    enabled !== settings.enabled ||
    token.trim() !== '' ||
    clearToken ||
    trimmedChat !== settings.chat_id;
  const tokenSet = settings.has_bot_token && !clearToken;

  const onSave = async () => {
    setFeedback(null);
    try {
      await save.mutateAsync({
        body: {
          enabled,
          bot_token: clearToken || token.trim() === '' ? null : token.trim(),
          clear_bot_token: clearToken,
          chat_id: trimmedChat,
        },
      });
      setToken('');
      setClearToken(false);
      setFeedback({ tone: 'success', text: t('settings.notify.saved') });
      void queryClient.invalidateQueries({
        queryKey: notificationSettingsQueryOptions().queryKey,
      });
    } catch (error) {
      setFeedback({
        tone: 'danger',
        text: `${t('settings.notify.saveFailed')}: ${mutationErrorText(error)}`,
      });
    }
  };

  const onTest = async () => {
    setFeedback(null);
    try {
      await sendTest.mutateAsync({});
      setFeedback({ tone: 'success', text: t('settings.notify.testSent') });
    } catch (error) {
      setFeedback({ tone: 'danger', text: mutationErrorText(error) });
    }
  };

  return (
    <Card title={t('settings.notify.title')} subtitle={t('settings.notify.subtitle')}>
      <form
        noValidate
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void onSave();
        }}
      >
        <SettingRow first label={t('settings.notify.enabled')}>
          <Switch checked={enabled} onChange={setEnabled} label={t('settings.notify.enabled')} />
        </SettingRow>
        <ApiKeyField
          label={t('settings.notify.token')}
          value={token}
          show={showToken}
          keySet={tokenSet}
          placeholder={
            clearToken
              ? t('settings.notify.tokenCleared')
              : !tokenSet
                ? t('settings.notify.tokenUnset')
                : // The eye reveals the stored token's hint — never the token itself.
                  showToken
                  ? t('settings.notify.tokenSetHint', { hint: settings.bot_token_hint ?? '••••' })
                  : t('settings.notify.tokenSet')
          }
          toggleLabel={t('settings.notify.toggleVisibility')}
          clearLabel={t('settings.notify.clearToken')}
          onChange={(value) => {
            setToken(value);
            setClearToken(false);
          }}
          onToggleShow={() => {
            setShowToken((shown) => !shown);
          }}
          onClear={() => {
            setClearToken(true);
            setToken('');
          }}
        />
        <div>
          <label htmlFor={chatInputId} className={FIELD_LABEL}>
            {t('settings.notify.chatId')}
          </label>
          <Input
            id={chatInputId}
            className="font-mono"
            inputMode="text"
            autoComplete="off"
            value={chatId}
            aria-invalid={chatInvalid}
            aria-describedby={chatHintId}
            onChange={(event) => {
              setChatId(event.target.value);
            }}
          />
          <p
            id={chatHintId}
            className={chatInvalid ? 'mt-2 type-small text-danger-deep' : 'mt-2 type-small'}
          >
            {chatInvalid ? t('settings.notify.chatIdInvalid') : t('settings.notify.chatIdHint')}
          </p>
        </div>
        {/* Says why "Send test" is disabled: it uses what is stored, not the drafts. */}
        {dirty ? <p className="type-small">{t('settings.notify.testNeedsSave')}</p> : null}
        {feedback ? (
          <Notice tone={feedback.tone} role={feedback.tone === 'danger' ? 'alert' : 'status'}>
            {feedback.text}
          </Notice>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2">
          <Button
            loading={sendTest.isPending}
            disabled={dirty || save.isPending}
            onClick={() => {
              void onTest();
            }}
          >
            {t('settings.notify.test')}
          </Button>
          <Button
            variant="primary"
            type="submit"
            loading={save.isPending}
            disabled={chatInvalid || !dirty}
          >
            {t('settings.notify.save')}
          </Button>
        </div>
      </form>
    </Card>
  );
}

export function NotificationsCard() {
  const { t } = useTranslation();
  const query = useQuery(notificationSettingsQueryOptions());
  if (query.isPending) return <p className="text-content-muted">{t('settings.loading')}</p>;
  if (query.isError) {
    return (
      <p role="alert" className="text-danger">
        {t('settings.error')}
      </p>
    );
  }
  return <NotificationsForm settings={query.data} />;
}
