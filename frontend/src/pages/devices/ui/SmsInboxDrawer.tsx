import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  manualInputPhoneOtpMutation,
  phoneTelephonyStatusQueryKey,
  smsInboxQueryKey,
  smsInboxQueryOptions,
  type SmsInboxMessage,
} from '@/entities/phone';
import { Badge, Button, CloseButton, EmptyState, Input, Modal, ModalHeader } from '@/shared/ui';

const POLL_INBOX_MS = 3000;
const COPY_FEEDBACK_MS = 2000;
const CUSTOM_TARGET = 'custom';
const MIN_CODE_LENGTH = 4;
const MAX_CODE_LENGTH = 8;

type CopyFeedback = { id: string; ok: boolean };

function formatSmsTimestamp(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

async function writeToClipboard(text: string): Promise<boolean> {
  if (typeof navigator === 'undefined' || !navigator.clipboard?.writeText) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function SmsInboxDrawer({ phoneId, onClose }: { phoneId: string; onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data } = useQuery({
    ...smsInboxQueryOptions({ query: { phone_id: phoneId } }),
    refetchInterval: POLL_INBOX_MS,
  });
  const manualType = useMutation(manualInputPhoneOtpMutation());
  const [copyFeedback, setCopyFeedback] = useState<CopyFeedback | null>(null);
  // Which button started the typing in flight (a message id or the custom field):
  // two messages can carry the same code, so the code itself cannot say.
  const [typingTarget, setTypingTarget] = useState<string | null>(null);
  const [customCode, setCustomCode] = useState('');

  useEffect(() => {
    if (copyFeedback === null) return undefined;
    const timer = window.setTimeout(() => setCopyFeedback(null), COPY_FEEDBACK_MS);
    return () => window.clearTimeout(timer);
  }, [copyFeedback]);

  const messages: SmsInboxMessage[] = Array.isArray(data?.messages) ? data.messages : [];

  const copyCode = (msg: SmsInboxMessage) => {
    if (!msg.code) return;
    void writeToClipboard(msg.code).then((ok) => setCopyFeedback({ id: msg.id, ok }));
  };

  const copyLabel = (msg: SmsInboxMessage) => {
    if (copyFeedback?.id !== msg.id) return t('telephony.inbox.copyCode');
    return copyFeedback.ok ? t('telephony.inbox.copied') : t('telephony.inbox.copyFailed');
  };

  const triggerManualType = (code: string, target: string) => {
    const trimmed = code.trim();
    if (trimmed.length < MIN_CODE_LENGTH) return;
    setTypingTarget(target);
    void manualType
      .mutateAsync({ path: { phone_id: phoneId }, body: { code: trimmed } })
      .then(() => {
        if (target === CUSTOM_TARGET) setCustomCode('');
      })
      // The global mutation cache toasts a refusal; the catch only keeps it handled.
      .catch(() => undefined)
      .finally(() => {
        setTypingTarget(null);
        void queryClient.invalidateQueries({
          queryKey: phoneTelephonyStatusQueryKey({ path: { phone_id: phoneId } }),
        });
        void queryClient.invalidateQueries({
          queryKey: smsInboxQueryKey({ query: { phone_id: phoneId } }),
        });
      });
  };

  return (
    <Modal variant="drawer-left" label={t('telephony.inbox.title')} onClose={onClose}>
      <ModalHeader title={t('telephony.inbox.title')}>
        <CloseButton aria-label={t('telephony.inbox.close')} onClick={onClose} />
      </ModalHeader>
      <div className="flex flex-1 flex-col gap-4 p-6">
        <div className="flex items-center gap-2">
          <Input
            size="sm"
            value={customCode}
            inputMode="numeric"
            aria-label={t('telephony.inbox.manualInputPlaceholder')}
            placeholder={t('telephony.inbox.manualInputPlaceholder')}
            onChange={(event) =>
              setCustomCode(event.target.value.replace(/[^0-9]/g, '').slice(0, MAX_CODE_LENGTH))
            }
          />
          <Button
            size="sm"
            variant="primary"
            disabled={customCode.length < MIN_CODE_LENGTH || typingTarget !== null}
            loading={typingTarget === CUSTOM_TARGET}
            onClick={() => triggerManualType(customCode, CUSTOM_TARGET)}
          >
            {t('telephony.inbox.manualType')}
          </Button>
        </div>

        {messages.length === 0 ? (
          <EmptyState boxed>{t('telephony.inbox.empty')}</EmptyState>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-3 p-0">
            {messages.map((msg) => (
              <li
                key={msg.id}
                className="flex flex-col gap-2 rounded-md border border-line bg-surface p-3"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="type-body-medium">{msg.sender}</span>
                  <span className="type-small">{formatSmsTimestamp(msg.timestamp)}</span>
                </div>
                <p className="m-0 type-body break-words">{msg.text}</p>
                {msg.code ? (
                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    <Badge tone="info" size="sm" className="tracking-code">
                      {msg.code}
                    </Badge>
                    <Button size="sm" variant="secondary" onClick={() => copyCode(msg)}>
                      {copyLabel(msg)}
                    </Button>
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={typingTarget !== null && typingTarget !== msg.id}
                      loading={typingTarget === msg.id}
                      onClick={() => triggerManualType(msg.code ?? '', msg.id)}
                    >
                      {t('telephony.inbox.manualType')}
                    </Button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}
