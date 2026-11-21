import { type ReactNode, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { HEADING_ICON_TILE } from '@/shared/design-system';
import { Icon, type IconName, IconButton, Modal } from '@/shared/ui';

import { ActionTuningCard } from './ActionTuningCard';
import { PaceCard } from './PaceCard';

// Одна иконка в шапке «В прогреве» и окно, которое она открывает.
export interface BoardTool {
  key: string;
  icon: IconName;
  title: string;
  subtitle?: string;
  content: ReactNode;
}

// Общее на весь прогрев — иконками у заголовка «В прогреве»: тонкая настройка
// действий и темп отсюда же, а переписку и каналы (другие слайсы) передаёт страница.
export function BoardTools({ extra = [] }: { extra?: BoardTool[] }) {
  const { t } = useTranslation();
  const [openKey, setOpenKey] = useState<string | null>(null);
  const tools: BoardTool[] = [
    {
      key: 'tune',
      icon: 'gear',
      title: t('warming.tune.title'),
      subtitle: t('warming.tune.subtitle'),
      content: <ActionTuningCard bare />,
    },
    {
      key: 'pace',
      icon: 'chart',
      title: t('warming.pace.title'),
      subtitle: t('warming.pace.subtitle'),
      content: <PaceCard bare />,
    },
    ...extra,
  ];
  const open = tools.find((tool) => tool.key === openKey);
  return (
    <div className="flex items-center gap-1">
      {tools.map((tool) => (
        <IconButton
          key={tool.key}
          shape="circle"
          aria-label={tool.title}
          title={tool.title}
          onClick={() => {
            setOpenKey(tool.key);
          }}
        >
          <Icon name={tool.icon} size={16} />
        </IconButton>
      ))}
      {open ? (
        <Modal
          size="table"
          label={open.title}
          onClose={() => {
            setOpenKey(null);
          }}
        >
          <div className="p-6">
            <div className="mb-1 flex items-start gap-3">
              <div className={HEADING_ICON_TILE}>
                <Icon name={open.icon} size={16} />
              </div>
              <div className="flex-1 type-h2">{open.title}</div>
            </div>
            {open.subtitle ? (
              <div className="mb-6 type-body text-content-muted">{open.subtitle}</div>
            ) : (
              <div className="mb-4" />
            )}
            {open.content}
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
