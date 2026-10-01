import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { HEADING_ICON_TILE } from '@/shared/design-system';
import {
  ModalHeader,
  ModalBody,
  ModalFooter,
  Badge,
  Button,
  Icon,
  IconButton,
  Input,
  Modal,
  Textarea,
} from '@/shared/ui';

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
    <Modal onClose={onClose} size="panel" label={t('neurocomment.modal.createCampaign.title')}>
      <ModalHeader className="flex items-center">
        <span className={HEADING_ICON_TILE}>
          <Icon name="plus" size={18} />
        </span>
        <div>
          <div className="type-dialog-title">{t('neurocomment.modal.createCampaign.title')}</div>
          <div className="mt-hair type-prose">{t('neurocomment.modal.createCampaign.sub')}</div>
        </div>
      </ModalHeader>

      <ModalBody>
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
          className="mb-lg"
          value={prompt}
          onChange={(event) => {
            setPrompt(event.target.value);
          }}
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
                contentGap="roomy"
                appearance="channel"
                bordered
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
          <Button variant="infoGhost" size="sm" shape="square" onClick={addChannel}>
            {t('neurocomment.modal.add')}
          </Button>
        </div>
      </ModalBody>

      <ModalFooter variant="inset" className="flex">
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
      </ModalFooter>
    </Modal>
  );
}
