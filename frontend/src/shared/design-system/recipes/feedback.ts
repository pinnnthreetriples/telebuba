// Один набор тонов на строчную плашку и на блочное уведомление.
//
// Badge и Notice — одна идея в двух формах: плашка не переносится и стоит в строке,
// уведомление — абзац на тонированной панели. Тон означал у них одно и то же и был набран
// дважды, поэтому `neutral` был только у одного, а рамка — только у другого, и никто не
// решал ни того, ни другого.
//
// Тоны: `neutral` — без смысла, просто счётчик или ярлык; `info` — живое состояние;
// `success`, `warning`, `danger` — исход.
//
// `info`, а не `primary`, и это не косметика: тон обратной связи и цвет ДЕЙСТВИЯ звались
// одним словом, притом что красятся они разными ступенями (`info-tint`/`info-strong`
// против `action-primary`) и меняются по разным причинам. У `IconButton` тон `primary`
// остаётся — там это и есть «главное действие», другая шкала и другой вопрос. У уведомления `neutral` намеренно
// нет: уведомление сообщает СМЫСЛ, и уведомление без смысла — это абзац, для которого
// есть карточка.
//
// Краска текста — рунг `deep`, а не базовый: базовый на своём же тоне мерит 2.97:1
// (success), 4.34:1 (danger) и 4.38:1 (primary), а плашка «удалён» в приложении набрана
// мелким. `deep` берёт пол AA, и `contrast.test.ts` проверяет обе стороны — что `deep`
// проходит и что базовый не проходит, чтобы «починку» нельзя было молча откатить.
import { cn } from '@/shared/lib/cn';

const TONE = {
  neutral: { tint: 'bg-canvas', ink: 'text-content-muted', line: 'border-line' },
  info: { tint: 'bg-info-tint', ink: 'text-info-strong', line: 'border-info-line' },
  success: { tint: 'bg-success-tint', ink: 'text-success-deep', line: 'border-success-line' },
  warning: { tint: 'bg-warning-tint', ink: 'text-warning-deep', line: 'border-warning-line' },
  danger: { tint: 'bg-danger-tint', ink: 'text-danger-deep', line: 'border-danger-line' },
} as const;

// Четыре тона, которые сообщают СМЫСЛ, — и это имя концепции, а не имя компонента.
// Раньше здесь было наоборот: авторитетным был `Tone` (все пять), а четвёрка называлась
// `NoticeTone`, то есть через компонент, который её первым попросил. Смысл при этом
// принадлежит не компоненту: те же четыре тона носят плашка, уведомление, столбик
// состояния прогрева и роли `on-success`/`on-warning`/`on-danger` в семантике. Имя,
// названное по первому потребителю, второму потребителю приходится либо переименовывать,
// либо повторять — WARM_STATUS повторил, парами классов.
export type FeedbackTone = Exclude<keyof typeof TONE, 'neutral'>;

// Те же четыре плюс `neutral` — ярлык, у которого смысла нет вовсе: счётчик, «черновик».
// Носит его только плашка; у уведомления `neutral` намеренно недоступен, см. шапку.
//
// Отдельного `Tone` больше нет: он был третьим именем того же набора (`Tone`,
// `BadgeTone`, `NoticeTone` на два множества), и `BadgeTone` объявлялся псевдонимом к
// нему в `Badge.tsx`. Множеств два, имён теперь тоже два.
export type BadgeTone = FeedbackTone | 'neutral';

/** Строчная плашка: заливка тона и его краска, без рамки. */
export function badgeTone(tone: BadgeTone): string {
  return cn(TONE[tone].tint, TONE[tone].ink);
}

/** Блочное уведомление: то же плюс рамка, которая отделяет его от карточки под ним. */
export function noticeTone(tone: FeedbackTone, bordered: boolean): string {
  return cn(TONE[tone].tint, TONE[tone].ink, bordered && cn('border', TONE[tone].line));
}
