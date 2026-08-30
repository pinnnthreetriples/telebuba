import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { NeuroshillingRole, NeuroshillingStep } from '@/shared/api';

import { ApproveModal } from './ApproveModal';

const ROLES: NeuroshillingRole[] = [
  { role_id: 'r1', name: 'Скептик', description: '', created_at: 'now' },
  { role_id: 'r2', name: 'Клиент', description: '', created_at: 'now' },
];

const STEPS: NeuroshillingStep[] = [
  {
    step_id: 's1',
    position: 1,
    kind: 'message',
    role_id: 'r1',
    text: 'а работает вообще?',
    delay_min_seconds: 60,
    delay_max_seconds: 180,
  },
  {
    step_id: 's2',
    position: 2,
    kind: 'message',
    role_id: 'r2',
    text: 'да, вожу им второй месяц',
    reply_to_position: 1,
    delay_min_seconds: 30,
    delay_max_seconds: 90,
  },
  {
    step_id: 's3',
    position: 3,
    kind: 'reaction',
    role_id: 'r1',
    emoji: '🔥',
    target_position: 2,
    delay_min_seconds: 10,
    delay_max_seconds: 30,
  },
];

function renderCard(props: Partial<Parameters<typeof ApproveModal>[0]> = {}) {
  return render(
    <ApproveModal
      roles={ROLES}
      steps={STEPS}
      status="draft"
      dirty={false}
      onRegenerate={() => undefined}
      onApprove={() => undefined}
      onClose={() => undefined}
      delays={null}
      onDelay={() => undefined}
      busy={false}
      {...props}
    />,
  );
}

test('every step becomes a bubble with its speaker and its own pause', () => {
  renderCard();

  expect(screen.getByText('да, вожу им второй месяц')).toBeInTheDocument();
  expect(screen.getAllByText('Скептик')).toHaveLength(2);
  // Twice: once as its own bubble, once quoted above the reply that points at it.
  expect(screen.getAllByText('а работает вообще?')).toHaveLength(2);
  expect(screen.getByText('реакция на #2')).toBeInTheDocument();
  expect(screen.getByText('30–90 с')).toBeInTheDocument();
});

test('the clock is the running sum of the mean pauses', () => {
  renderCard();

  // 120, then +60, then +20 — mm:ss, and the same sum in the footer.
  expect(screen.getByText('+2:00')).toBeInTheDocument();
  expect(screen.getByText('+3:00')).toBeInTheDocument();
  expect(screen.getByText('+3:20')).toBeInTheDocument();
  expect(screen.getByText('≈ 3:20 на весь диалог')).toBeInTheDocument();
});

test('an approved scenario carries the badge', () => {
  renderCard({ status: 'approved' });

  expect(screen.getByText('✓ Утверждён')).toBeInTheDocument();
});

test('unsaved edits are named rather than previewed', () => {
  renderCard({ dirty: true });

  // The card renders what is STORED, so it has to say when that is not what the
  // form holds.
  expect(
    screen.getByText('Есть несохранённые правки — в превью показан сохранённый сценарий.'),
  ).toBeInTheDocument();
});

test('an empty scenario shows the empty state and cannot be played', () => {
  renderCard({ steps: [] });

  expect(
    screen.getByText('Превью пока нечего показать. Сгенерируйте диалог или напишите его руками.'),
  ).toBeInTheDocument();
  expect(screen.getByText('Проиграть')).toBeDisabled();
});

test('a step whose role was deleted still renders, unattributed', () => {
  renderCard({
    steps: [{ step_id: 'x', position: 1, kind: 'message', text: 'ничей', role_id: null }],
  });

  expect(screen.getByText('Без роли')).toBeInTheDocument();
  expect(screen.getByText('ничей')).toBeInTheDocument();
});

test('a loose reaction says only that it reacted', () => {
  renderCard({ steps: [{ step_id: 'x', position: 1, kind: 'reaction', role_id: 'r1' }] });

  expect(screen.getByText('реакция')).toBeInTheDocument();
});

test('playing restages the bubbles instead of running a timer per row', async () => {
  renderCard();
  // Диалог уходит в портал на body, поэтому `container` вызова рендера пуст: пузыри
  // ищутся в документе.
  const bubbles = () => [...document.body.querySelectorAll<HTMLElement>('.tb-fadeup')];
  const staggerOf = () => bubbles().map((node) => node.style.animationDelay);
  const before = bubbles();
  expect(staggerOf()).toEqual(['0s', '0.12s', '0.24s']);

  await userEvent.click(screen.getByText('Проиграть'));

  // The bubble keys carry the play counter, so every row is a NEW element — which is
  // what makes the CSS enter animation run again. Compared by node identity because
  // the rendered markup is identical either way: an inert button would leave the same
  // delays and the same text on screen and say nothing about a replay.
  expect(bubbles()).toHaveLength(before.length);
  expect(bubbles().some((node, index) => node === before[index])).toBe(false);
  // Nothing else moves: the same stagger, the same lines.
  expect(staggerOf()).toEqual(['0s', '0.12s', '0.24s']);
  expect(screen.getAllByText('а работает вообще?')).toHaveLength(2);
});

// Паузы держит состояние, как их держит страница: поле управляемое, и со статичным
// пропом каждый следующий символ дописывался бы к откатившемуся значению.
function PauseHarness({ onDelay }: { onDelay: (i: number, min: number, max: number) => void }) {
  const [delays, setDelays] = useState([
    { min: 60, max: 180 },
    { min: 45, max: 120 },
    { min: 10, max: 30 },
  ]);
  return (
    <ApproveModal
      roles={ROLES}
      steps={STEPS}
      status="draft"
      dirty={false}
      onRegenerate={() => undefined}
      onApprove={() => undefined}
      onClose={() => undefined}
      delays={delays}
      onDelay={(index, min, max) => {
        setDelays((list) => list.map((item, at) => (at === index ? { min, max } : item)));
        onDelay(index, min, max);
      }}
      busy={false}
    />
  );
}

test('the pause between messages is editable in place, and the pair cannot invert', async () => {
  // Утверждают прочитанное, поэтому реплики приходят с сервера и правке не подлежат.
  // Пауза — ручка, и крутят её ровно тогда, когда диалог перед глазами.
  const onDelay = vi.fn();
  render(<PauseHarness onDelay={onDelay} />);

  const min = screen.getByLabelText('Минимальная пауза перед шагом 2');
  expect(min).toHaveValue(45);

  await userEvent.clear(min);
  await userEvent.type(min, '300');
  // Минимум перерос максимум — максимум едет за ним, иначе схема ответит 422.
  expect(onDelay).toHaveBeenLastCalledWith(1, 300, 300);
  expect(screen.getByLabelText('Максимальная пауза перед шагом 2')).toHaveValue(300);
});

test('without a draft that lines up step for step, the pause stays read-only', () => {
  // Сопоставление идёт ПО ИНДЕКСУ: подписать чужой шаг хуже, чем не дать его тронуть.
  renderCard({ delays: null });
  expect(screen.queryByLabelText('Минимальная пауза перед шагом 2')).toBeNull();
  expect(screen.getByText('30–90 с')).toBeInTheDocument();
});

test('regenerate asks the page and stands down while a request is in flight', async () => {
  const onRegenerate = vi.fn();
  const { rerender } = renderCard({ onRegenerate });

  await userEvent.click(screen.getByText('Перегенерировать'));
  expect(onRegenerate).toHaveBeenCalledTimes(1);

  rerender(
    <ApproveModal
      roles={ROLES}
      steps={STEPS}
      status="draft"
      dirty={false}
      onRegenerate={onRegenerate}
      onApprove={() => undefined}
      onClose={() => undefined}
      delays={null}
      onDelay={() => undefined}
      busy
    />,
  );
  expect(screen.getByText('Перегенерировать')).toBeDisabled();
});
