import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Badge, Button, Icon, IconButton, Input, Modal, ModalHeader, Textarea } from '@/shared/ui';

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

  // The dialog opens empty, so anything typed — a channel still in its box included — is
  // an edit closing would lose.
  const dirty =
    name.trim() !== '' || prompt.trim() !== '' || channels.length > 0 || channelInput.trim() !== '';

  return (
    <Modal
      onClose={onClose}
      dirty={dirty}
      size="panel"
      label={t('neurocomment.modal.createCampaign.title')}
    >
      {(close) => (
        <>
          <ModalHeader
            title={t('neurocomment.modal.createCampaign.title')}
            subtitle={t('neurocomment.modal.createCampaign.sub')}
            icon={<Icon name="plus" size={18} />}
          />

          <div className="px-6 pb-6 pt-6">
            <div className="mb-2 type-body-medium">
              {t('neurocomment.modal.createCampaign.nameLabel')}
            </div>
            <Input
              className="mb-4"
              value={name}
              onChange={(event) => {
                setName(event.target.value);
              }}
              placeholder={t('neurocomment.modal.createCampaign.namePlaceholder')}
              aria-label={t('neurocomment.modal.createCampaign.nameLabel')}
            />

            <div className="mb-2 type-body-medium">
              {t('neurocomment.modal.createCampaign.promptLabel')}
            </div>
            <Textarea
              className="mb-4 font-[inherit]"
              value={prompt}
              onChange={(event) => {
                setPrompt(event.target.value);
              }}
              placeholder={t('neurocomment.modal.createCampaign.promptPlaceholder')}
              aria-label={t('neurocomment.modal.createCampaign.promptLabel')}
            />

            <div className="mb-2 type-body-medium">
              {t('neurocomment.modal.createCampaign.channelsLabel')}
            </div>
            <div className="mb-3 type-small">
              {t('neurocomment.modal.createCampaign.channelsHint')}
            </div>
            {channels.length > 0 ? (
              <div className="mb-3 flex flex-wrap gap-2">
                {channels.map((channel, index) => (
                  <Badge
                    size="md"
                    className="gap-2 border border-line text-content-secondary"
                    key={`${channel}-${String(index)}`}
                  >
                    {channel}
                    <IconButton
                      size="sm"
                      shape="circle"
                      aria-label={t('neurocomment.channels.remove')}
                      onClick={() => {
                        setChannels((list) => list.filter((_, i) => i !== index));
                      }}
                    >
                      <Icon name="close" size={16} />
                    </IconButton>
                  </Badge>
                ))}
              </div>
            ) : null}
            <div className="flex gap-2">
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
                className="rounded-md bg-info-tint text-info-strong"
                onClick={addChannel}
              >
                {t('neurocomment.modal.add')}
              </Button>
            </div>
          </div>

          <div className="flex gap-2 border-t border-canvas px-6 pb-6 pt-4">
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
            <Button className="flex-1" onClick={close}>
              {t('neurocomment.modal.cancel')}
            </Button>
          </div>
        </>
      )}
    </Modal>
  );
}
