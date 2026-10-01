import type { ReactNode } from 'react';

// Round translucent control floating over the photo / story viewer's dark stage.
// Stops propagation so a press never also reaches the stage's tap zones.
export function ViewerButton({
  label,
  onClick,
  className,
  children,
}: {
  label: string;
  onClick: () => void;
  className: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      onPointerDown={(event) => {
        event.stopPropagation();
      }}
      onPointerUp={(event) => {
        event.stopPropagation();
      }}
      className={`absolute z-raised flex size-tile items-center justify-center rounded-full bg-black/55 text-on-inverse transition-colors hover:bg-black/70 ${className}`}
    >
      {children}
    </button>
  );
}
