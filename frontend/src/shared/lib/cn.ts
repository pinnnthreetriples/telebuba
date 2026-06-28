import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

// The shadcn/ui class-merge helper: clsx for conditional joins, tailwind-merge
// to dedupe conflicting Tailwind utilities (last one wins).
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
