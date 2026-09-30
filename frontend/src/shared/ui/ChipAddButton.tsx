import type { ComponentProps } from 'react';

import { cn } from '@/shared/lib/cn';

import { Button } from './Button';

type ChipAddButtonProps = Omit<ComponentProps<typeof Button>, 'variant' | 'size'>;

// Пунктирный «+ …» в конце ряда чипов. Размер и вес — часть образца, а не выбор места
// вызова: на странице прогрева он был `xs` и казался мельче чипов рядом, а у
// нейрокомментинга — `md` (эталон). Теперь у ряда одна высота, и она чипова.
export function ChipAddButton({ className, ...props }: ChipAddButtonProps) {
  return (
    <Button
      variant="dashedMuted"
      size="md"
      className={cn('px-md font-medium', className)}
      {...props}
    />
  );
}
