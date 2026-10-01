export { SettingRow as Row } from '@/shared/ui';

export function Eyebrow({ title, caption }: { title: string; caption?: string }) {
  return (
    <div className="flex flex-wrap items-baseline gap-sm pb-sm">
      <span className="type-eyebrow">{title}</span>
      {caption === undefined ? null : <span className="type-caption">{caption}</span>}
    </div>
  );
}
