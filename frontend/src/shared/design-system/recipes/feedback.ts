// Один набор тонов на строчную плашку и на блочное уведомление.
//
// Badge и Notice — одна идея в двух формах: плашка не переносится и стоит в строке,
// уведомление — абзац на тонированной панели. Тон означал у них одно и то же и был набран
// дважды, поэтому `neutral` был только у одного, а рамка — только у другого, и никто не
// решал ни того, ни другого.
//
// Пять тонов: `neutral` — без смысла, просто счётчик или ярлык; `primary` — живое
// состояние; `success`, `warning`, `danger` — исход. У уведомления `neutral` намеренно
// нет: уведомление сообщает СМЫСЛ, и уведомление без смысла — это абзац, для которого
// есть карточка.
//
// Краска текста — рунг `deep`, а не базовый: базовый на своём же тоне мерит 2.97:1
// (success), 4.34:1 (danger) и 4.38:1 (primary), а плашка «удалён» в приложении набрана
// мелким. `deep` берёт пол AA, и `contrast.test.ts` проверяет обе стороны — что `deep`
// проходит и что базовый не проходит, чтобы «починку» нельзя было молча откатить.
import { cn } from '@/shared/lib/cn';

const TONE = {
  neutral: { tint: 'bg-canvas', ink: 'text-ink-muted', line: 'border-line' },
  primary: { tint: 'bg-primary-tint', ink: 'text-primary-deep', line: 'border-primary-line' },
  success: { tint: 'bg-success-tint', ink: 'text-success-deep', line: 'border-success-line' },
  warning: { tint: 'bg-warning-tint', ink: 'text-warning-deep', line: 'border-warning-line' },
  danger: { tint: 'bg-danger-tint', ink: 'text-danger-deep', line: 'border-danger-line' },
} as const;

export type Tone = keyof typeof TONE;

// Уведомление не носит `neutral`; см. шапку.
export type NoticeTone = Exclude<Tone, 'neutral'>;

/** Строчная плашка: заливка тона и его краска, без рамки. */
export function badgeTone(tone: Tone): string {
  return cn(TONE[tone].tint, TONE[tone].ink);
}

/** Блочное уведомление: то же плюс рамка, которая отделяет его от карточки под ним. */
export function noticeTone(tone: NoticeTone, bordered: boolean): string {
  return cn(TONE[tone].tint, TONE[tone].ink, bordered && cn('border', TONE[tone].line));
}
