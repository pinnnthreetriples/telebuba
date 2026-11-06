import { useTranslation } from 'react-i18next';

import { type CollageCell, layoutsForCount } from './storyCollageLayouts';

// A 9:16 mini-preview of a collage layout: each cell drawn as a rounded rect
// inside a framed portrait canvas. Selected state paints the cells in primary.
function LayoutIcon({ cells, selected }: { cells: readonly CollageCell[]; selected: boolean }) {
  const w = 26;
  const h = 46;
  const gap = 1.4;
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      {/* Tokens, not hexes: the selected frame matches the button's own
          `border-action-primary`, and the cells inherit the button's `text-action-primary`. */}
      <rect
        x="0.5"
        y="0.5"
        width={w - 1}
        height={h - 1}
        rx="4"
        fill="none"
        className={selected ? 'stroke-action-primary' : 'stroke-content-subtle'}
      />
      {cells.map(([x, y, cw, ch], i) => (
        <rect
          key={i}
          x={x * w + gap}
          y={y * h + gap}
          width={cw * w - gap * 2}
          height={ch * h - gap * 2}
          rx="1.4"
          className={selected ? 'fill-current' : 'fill-content-subtle'}
        />
      ))}
    </svg>
  );
}

// Layout picker — only for a 2..6 photo collage.
export function StoryCollageLayoutPicker({
  count,
  value,
  onChange,
  disabled = false,
}: {
  count: number;
  value: string | null;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="mt-4">
      <div className="mb-2 type-body-medium text-content-secondary">
        {t('accounts.addStory.layout')}
      </div>
      <div className="flex flex-wrap gap-2">
        {layoutsForCount(count).map((layout) => {
          const selected = value === layout.id;
          return (
            <button
              key={layout.id}
              type="button"
              disabled={disabled}
              onClick={() => {
                onChange(layout.id);
              }}
              aria-label={t('accounts.addStory.layoutOption', { id: layout.id })}
              aria-pressed={selected}
              // eslint-disable-next-line design-tokens/no-raw-values -- see the note in the rule: the collage-layout tile's own box, one component's internal layout
              className={`flex h-[62px] w-[46px] items-center justify-center rounded-sm border text-action-primary transition disabled:opacity-50 ${selected ? 'border-action-primary bg-info-tint' : 'border-line bg-surface-card'}`}
            >
              <LayoutIcon cells={layout.cells} selected={selected} />
            </button>
          );
        })}
      </div>
    </div>
  );
}
