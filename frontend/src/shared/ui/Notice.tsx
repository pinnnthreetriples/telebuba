import type { ReactNode } from 'react';

import { type FeedbackTone, noticeTone } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

// The tinted block that explains something on the screen it explains: a failed
// action, a warning about what a button will do, a hint under a form. Thirty-four
// of them, and the same three decisions were re-taken each time — which tint,
// whether it gets a border, and which of the semantic's rungs the text is in.
//
// `role="status"` is not set here: most of these are static prose that was on the
// screen before the operator looked at it, and a live region that announces itself
// on every render is worse than none. A notice that reports the outcome of an
// action passes `role="alert"` itself.
// Тон приходит из `recipes/feedback.ts` — того же набора, что у Badge. `neutral` здесь
// намеренно недоступен: уведомление сообщает СМЫСЛ, и уведомление без смысла — это абзац,
// для которого есть карточка. Тип `FeedbackTone` этот запрет и выражает: `neutral` в него
// не входит, и входить не может — это набор ТОНОВ СО СМЫСЛОМ, а плашка носит их плюс один.

export function Notice({
  tone = 'info',
  bordered = true,
  className,
  children,
  ...rest
}: {
  tone?: FeedbackTone;
  // The border is what separates a notice from the card behind it. It comes off
  // for the ones nested inside a panel that already has one.
  bordered?: boolean;
  className?: string;
  children?: ReactNode;
} & Omit<React.HTMLAttributes<HTMLDivElement>, 'className'>) {
  return (
    <div
      className={cn('rounded-lg px-md py-md text-body', noticeTone(tone, bordered), className)}
      {...rest}
    >
      {children}
    </div>
  );
}
