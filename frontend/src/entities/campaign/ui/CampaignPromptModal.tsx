import { dialogTitle } from '@/shared/design-system';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  ModalHeader,
  ModalFooter,
  ModalBody,
  Button,
  CloseButton,
  Icon,
  IconButton,
  Modal,
  Textarea,
} from '@/shared/ui';

export interface PromptAccount {
  account_id: string;
  phone: string;
  channel: string;
  initials: string;
}

// Design modal: campaign-prompt (L1321-1371) — edit the LLM prompt, see the
// accounts attached to the campaign, save with a check→"Сохранено" swap. A
// nested confirm guards removing an account from the campaign.
export function CampaignPromptModal({
  campaignName,
  initialPrompt,
  accounts,
  onClose,
  onSave,
  onRemoveAccount,
}: {
  campaignName: string;
  initialPrompt: string;
  accounts: PromptAccount[];
  onClose: () => void;
  onSave: (prompt: string) => void;
  onRemoveAccount: (accountId: string) => void;
}) {
  const { t } = useTranslation();
  const [prompt, setPrompt] = useState(initialPrompt);
  const [saved, setSaved] = useState(false);
  const [confirm, setConfirm] = useState<PromptAccount | null>(null);

  const save = () => {
    onSave(prompt);
    setSaved(true);
    setTimeout(onClose, 650);
  };

  return (
    <Modal onClose={onClose} size="form" label={t('neurocomment.modal.campaignPrompt.title')}>
      <ModalBody variant="form">
        <ModalHeader variant="inline" className="mb-tight justify-between">
          <span className="type-dialog-title">{t('neurocomment.modal.campaignPrompt.title')}</span>
          <CloseButton aria-label={t('neurocomment.modal.close')} onClick={onClose} />
        </ModalHeader>
        <div className="mb-lg type-prose">
          {t('neurocomment.modal.campaignPrompt.sub', { name: campaignName })}
        </div>
        <Textarea
          value={prompt}
          onChange={(event) => {
            setPrompt(event.target.value);
          }}
          placeholder={t('neurocomment.modal.campaignPrompt.placeholder')}
          // Its own name, not the dialog's: two elements sharing one accessible
          // name is what made getByLabelText ambiguous, and "Campaign prompt"
          // announced twice tells a screen-reader user nothing about the field.
          aria-label={t('neurocomment.modal.campaignPrompt.promptLabel')}
          variant="prompt"
        />

        <div className="my-xl mb-md flex items-center justify-between">
          <span className="type-item-title text-content-secondary">
            {t('neurocomment.modal.campaignPrompt.accounts')}
          </span>
          <span className="rounded-full bg-info-tint px-sm py-hair text-tiny font-semibold text-info-strong">
            {accounts.length}
          </span>
        </div>
        {accounts.length > 0 ? (
          <div className="tb-scroll flex max-h-feed flex-col gap-sm overflow-y-auto rounded-lg border border-canvas bg-surface p-tight">
            {accounts.map((account) => (
              <div
                key={account.account_id}
                className="flex items-center gap-md rounded-md border border-canvas bg-surface-card px-md py-sm"
              >
                <span className="flex size-icon shrink-0 items-center justify-center rounded-full bg-info-tint text-tiny font-bold text-info-strong">
                  {account.initials}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate type-card-title">{account.phone}</div>
                  <div className="mt-px type-caption">{account.channel}</div>
                </div>
                <span className="size-dot shrink-0 rounded-full bg-success" />
                <IconButton
                  size="md"
                  tone="danger"
                  aria-label={t('neurocomment.modal.campaignPrompt.removeAccount')}
                  onClick={() => {
                    setConfirm(account);
                  }}
                >
                  <Icon name="trash" size={16} />
                </IconButton>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-line-strong bg-surface p-lg text-center type-prose">
            {t('neurocomment.modal.campaignPrompt.empty')}
          </div>
        )}

        <ModalFooter variant="plain" className="mt-xl">
          <Button variant={saved ? 'saved' : 'primary'} onClick={save}>
            {saved ? (
              <span className="inline-flex items-center gap-sm">
                <span className="inline-flex tb-swapin">
                  <Icon name="check" size={16} />
                </span>
                <span className="inline-block tb-swapin-late">{t('neurocomment.modal.saved')}</span>
              </span>
            ) : (
              t('neurocomment.modal.save')
            )}
          </Button>
          <Button onClick={onClose}>{t('neurocomment.modal.cancel')}</Button>
        </ModalFooter>
      </ModalBody>

      {confirm ? (
        <Modal
          onClose={() => {
            setConfirm(null);
          }}
          size="confirm"
          label={t('neurocomment.modal.campaignPrompt.removeTitle')}
        >
          <ModalBody variant="form">
            <div className={dialogTitle()}>
              {t('neurocomment.modal.campaignPrompt.removeTitle')}
            </div>
            <div className="mb-xl type-dialog-body">
              {t('neurocomment.modal.campaignPrompt.removeBody', {
                phone: confirm.phone,
                channel: confirm.channel,
              })}
            </div>
            <ModalFooter variant="plain">
              <Button
                onClick={() => {
                  setConfirm(null);
                }}
              >
                {t('neurocomment.modal.cancel')}
              </Button>
              <Button
                variant="danger"
                onClick={() => {
                  onRemoveAccount(confirm.account_id);
                  setConfirm(null);
                }}
              >
                {t('neurocomment.modal.campaignPrompt.removeConfirm')}
              </Button>
            </ModalFooter>
          </ModalBody>
        </Modal>
      ) : null}
    </Modal>
  );
}
