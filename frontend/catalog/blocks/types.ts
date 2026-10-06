import type { ReactNode } from 'react';

export type BlockDemo = {
  id: string;
  // Имя компонента: по нему генератор находит файл и все места, где блок стоит.
  name: string;
  variants: { label: string; node: ReactNode }[];
};

export const noop = () => undefined;
