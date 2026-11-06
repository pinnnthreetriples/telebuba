import { useTranslation } from 'react-i18next';

import { Icon, IconButton, type IconName } from '@/shared/ui';

import type { StoryEntityType } from '../model/types';

interface ToolbarItem {
  type: StoryEntityType;
  icon: IconName;
  label: string;
  shortcut: string;
}

const ITEMS: readonly ToolbarItem[] = [
  { type: 'bold', icon: 'bold', label: 'bold', shortcut: 'Ctrl+B' },
  { type: 'italic', icon: 'italic', label: 'italic', shortcut: 'Ctrl+I' },
  { type: 'underline', icon: 'underline', label: 'underline', shortcut: 'Ctrl+U' },
  { type: 'strike', icon: 'strikethrough', label: 'strike', shortcut: 'Ctrl+Shift+X' },
  { type: 'spoiler', icon: 'eye-off', label: 'spoiler', shortcut: 'Ctrl+Shift+P' },
  { type: 'code', icon: 'code', label: 'code', shortcut: 'Ctrl+Shift+M' },
  { type: 'text_url', icon: 'link', label: 'link', shortcut: 'Ctrl+K' },
];

export function CaptionToolbar({
  active,
  hasSelection,
  disabled,
  onToggle,
}: {
  active: ReadonlySet<StoryEntityType>;
  hasSelection: boolean;
  disabled: boolean;
  onToggle: (type: StoryEntityType) => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      role="toolbar"
      aria-label={t('storyComposer.caption.toolbar')}
      className="flex flex-wrap gap-1"
    >
      {ITEMS.map((item) => {
        const pressed = active.has(item.type);
        const name = t(`storyComposer.caption.${item.label}`);
        return (
          <IconButton
            key={item.type}
            aria-label={name}
            title={
              item.type === 'text_url' && !hasSelection
                ? t('storyComposer.caption.linkNeedsSelection')
                : `${name} (${item.shortcut})`
            }
            aria-pressed={pressed}
            disabled={disabled || !hasSelection}
            className={pressed ? 'border-info-line bg-info-tint text-info-strong' : undefined}
            // Keep the selection: a button press would otherwise steal focus first.
            onMouseDown={(event) => {
              event.preventDefault();
            }}
            onClick={() => {
              onToggle(item.type);
            }}
          >
            <Icon name={item.icon} size={14} />
          </IconButton>
        );
      })}
    </div>
  );
}
