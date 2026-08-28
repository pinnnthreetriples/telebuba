import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Badge, Button, Icon, Input, Modal, Textarea } from '@/shared/ui';

// Design modal: create-campaign (L1424-1458) — name + LLM prompt + a list of
// campaign channels added as chips.
export function CreateCampaignModal({
  onClose,
  onCreate,
}: {
  onClose: () => void;
  onCreate: (input: { name: string; prompt: string; channels: string[] }) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [prompt, setPrompt] = useState('');
  const [channels, setChannels] = useState<string[]>([]);
  const [channelInput, setChannelInput] = useState('');

  const addChannel = () => {
    const value = channelInput.trim();
    if (!value) return;
    setChannels((list) => [...list, value]);
    setChannelInput('');
  };

  return (
    <Modal
      onClose={onClose}
      className="w-panel"
      label={t('neurocomment.modal.createCampaign.title')}
    >
      <div className="flex items-center gap-md border-b border-line-row px-2xl pb-lg pt-xl">
        <span className="flex size-tile shrink-0 items-center justify-center rounded-lg bg-primary-tint text-primary-deep">
          <Icon name="plus" size={18} />
        </span>
        <div>
          <div className="type-dialog-title">{t('neurocomment.modal.createCampaign.title')}</div>
          <div className="mt-hair type-prose">{t('neurocomment.modal.createCampaign.sub')}</div>
        </div>
      </div>

      <div className="px-2xl pb-xl pt-xl">
        <div className="mb-sm type-item-title">
          {t('neurocomment.modal.createCampaign.nameLabel')}
        </div>
        <Input
          className="mb-lg"
          value={name}
          onChange={(event) => {
            setName(event.target.value);
          }}
          placeholder={t('neurocomment.modal.createCampaign.namePlaceholder')}
          aria-label={t('neurocomment.modal.createCampaign.nameLabel')}
        />

        <div className="mb-sm type-item-title">
          {t('neurocomment.modal.createCampaign.promptLabel')}
        </div>
        <Textarea
          className="mb-lg resize-y font-[inherit]"
          value={prompt}
          onChange={(event) => {
            setPrompt(event.target.value);
          }}
          rows={4}
          placeholder={t('neurocomment.modal.createCampaign.promptPlaceholder')}
          aria-label={t('neurocomment.modal.createCampaign.promptLabel')}
        />

        <div className="mb-sm type-item-title">
          {t('neurocomment.modal.createCampaign.channelsLabel')}
        </div>
        <div className="mb-md type-caption">
          {t('neurocomment.modal.createCampaign.channelsHint')}
        </div>
        {channels.length > 0 ? (
          <div className="mb-md flex flex-wrap gap-sm">
            {channels.map((channel, index) => (
              <Badge
                size="md"
                className="gap-sm border border-line text-ink-body"
                key={`${channel}-${String(index)}`}
              >
                {channel}
                <button
                  type="button"
                  aria-label={t('neurocomment.channels.remove')}
                  onClick={() => {
                    setChannels((list) => list.filter((_, i) => i !== index));
                  }}
                  className="cursor-pointer text-body leading-none text-ink-subtle"
                >
                  ×
                </button>
              </Badge>
            ))}
          </div>
        ) : null}
        <div className="flex gap-sm">
          <Input
            className="flex-1"
            value={channelInput}
            onChange={(event) => {
              setChannelInput(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                addChannel();
              }
            }}
            placeholder={t('neurocomment.channels.placeholder')}
            aria-label={t('neurocomment.channels.placeholder')}
          />
          <Button
            variant="ghost"
            size="sm"
            className="rounded-lg bg-primary-tint text-primary-deep"
            onClick={addChannel}
          >
            {t('neurocomment.modal.add')}
          </Button>
        </div>
      </div>

      <div className="flex gap-sm border-t border-line-row px-2xl pb-xl pt-lg">
        <Button
          variant="primary"
          className="flex-1"
          disabled={!name.trim() || !prompt.trim()}
          onClick={() => {
            onCreate({ name: name.trim(), prompt: prompt.trim(), channels });
            onClose();
          }}
        >
          {t('neurocomment.modal.createCampaign.confirm')}
        </Button>
        <Button className="flex-1" onClick={onClose}>
          {t('neurocomment.modal.cancel')}
        </Button>
      </div>
    </Modal>
  );
}
