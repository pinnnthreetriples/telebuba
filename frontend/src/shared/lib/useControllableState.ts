import { useCallback, useRef, useState } from 'react';

// Состояние, которым компонент владеет сам, пока вызывающий не возьмёт его себе.
//
// `value` задан — компонент управляемый: он показывает `value` и только СООБЩАЕТ о смене
// через `onValueChange`, а решает вызывающий. `value` не задан — компонент держит своё
// состояние, начиная с `defaultValue`, и сообщает о смене так же. Сеттер принимает и
// значение, и функцию от текущего, как `useState`.
//
// Значение, равное текущему (`Object.is`), не сообщается вовсе: «сохранить то же время»
// или «закрыть закрытое» — не событие, и вызывающий не должен получать его дважды.
//
// Текущее значение читается через ref, поэтому функция-сеттер видит последнее значение,
// даже если её вызвали дважды до перерисовки, а сам сеттер стабилен между рендерами и
// годится в зависимости эффекта.
export function useControllableState<T>({
  value,
  defaultValue,
  onValueChange,
}: {
  value?: T;
  defaultValue: T;
  onValueChange?: (value: T) => void;
}): [T, (next: T | ((current: T) => T)) => void] {
  const [own, setOwn] = useState(defaultValue);
  const controlled = value !== undefined;
  const current = controlled ? value : own;

  const latest = useRef(current);
  latest.current = current;
  const notify = useRef(onValueChange);
  notify.current = onValueChange;

  const set = useCallback(
    (next: T | ((current: T) => T)) => {
      const resolved =
        typeof next === 'function' ? (next as (current: T) => T)(latest.current) : next;
      if (Object.is(latest.current, resolved)) return;
      latest.current = resolved;
      if (!controlled) setOwn(resolved);
      notify.current?.(resolved);
    },
    [controlled],
  );

  return [current, set];
}
