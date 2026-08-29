import type { ReactNode } from 'react';

import { surface } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

// The app's card surface: white, hairline border, `rounded-card`. It lived as a
// local component on the settings page while fourteen other places spelled the
// same three classes out, which is how one of them ended up a shade off.
//
// `CollapsibleCard` is the other one — a card whose body folds away, with a header
// row it owns. This is the plain surface; nothing is nested in it by default.
//
// Внешнего отступа у карточки нет и не будет: `mb` был пропом, то есть карточка
// диктовала расстояние до того, что стоит ПОД ней, ничего об этом не зная. Настройки
// тратили его пять раз и на четырёх ступень `lg`, на пятой `xl` — расхождение, которое
// именно так и возникает. Расстоянием управляет родитель, у него на это есть `gap`.
export function Card({
  title,
  subtitle,
  className = 'px-xl py-xl',
  children,
  ...rest
}: {
  title?: string;
  subtitle?: string;
  // Padding, kept as a prop because a card that holds a table pads its rows
  // instead of itself, and one that holds a form pads itself.
  className?: string;
  children: ReactNode;
} & Omit<React.HTMLAttributes<HTMLDivElement>, 'className' | 'title'>) {
  return (
    <div className={cn(surface('card'), className)} {...rest}>
      {title ? <div className="mb-xs text-body font-semibold">{title}</div> : null}
      {subtitle ? <div className="mb-lg text-body text-content-subtle">{subtitle}</div> : null}
      {children}
    </div>
  );
}
