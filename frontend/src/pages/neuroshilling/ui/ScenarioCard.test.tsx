import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { ScenarioCard } from './ScenarioCard';
import type { DraftStep, ScenarioDraft } from './scenarioDraft';

function step(key: string, patch: Partial<DraftStep> = {}): DraftStep {
  return {
    key,
    kind: 'message',
    roleId: 'r1',
    text: `текст ${key}`,
    replyToPosition: null,
    targetPosition: null,
    emoji: null,
    delayMinSeconds: 60,
    delayMaxSeconds: 180,
    ...patch,
  };
}

const DRAFT: ScenarioDraft = {
  campaignId: 'c1',
  mode: 'campaign',
  topic: 'про сервис доставки',
  uniqueMessages: true,
  useChatContext: false,
  mediaMessageLink: '',
  mediaStepPosition: null,
  roles: [
    { roleId: 'r1', name: 'Скептик', description: 'сомневается' },
    { roleId: 'r2', name: 'Клиент', description: 'делится опытом' },
  ],
  steps: [step('s1'), step('s2', { roleId: 'r2' })],
};

interface Options {
  draft?: ScenarioDraft;
  status?: 'draft' | 'approved';
  dirty?: boolean;
  busy?: boolean;
  onGenerate?: () => void;
  onSave?: () => void;
  onApprove?: () => void;
  onPersonaCount?: (value: number) => void;
}

// The card is controlled, so the harness owns the draft the way the page does —
// otherwise every edit would be discarded and nothing below could be observed.
function Harness({ options }: { options: Options }) {
  const [draft, setDraft] = useState(options.draft ?? DRAFT);
  return (
    <ScenarioCard
      draft={draft}
      onDraft={setDraft}
      status={options.status ?? 'draft'}
      dirty={options.dirty ?? false}
      personaCount={3}
      stepCount={8}
      onPersonaCount={options.onPersonaCount ?? (() => undefined)}
      onStepCount={() => undefined}
      onGenerate={options.onGenerate ?? (() => undefined)}
      onSave={options.onSave ?? (() => undefined)}
      onApprove={options.onApprove ?? (() => undefined)}
      busy={options.busy ?? false}
    />
  );
}

function renderCard(options: Options = {}) {
  return render(<Harness options={options} />);
}

test('the roles and the dialogue are laid out in order', () => {
  renderCard();

  expect(screen.getByLabelText('Название роли 1')).toHaveValue('Скептик');
  expect(screen.getByLabelText('Персона роли 2')).toHaveValue('делится опытом');
  expect(screen.getByLabelText('Текст шага 1')).toHaveValue('текст s1');
  expect(screen.getByLabelText('Роль шага 2')).toHaveValue('r2');
});

test('an approved scenario says on THIS card that editing it will drop the approval', () => {
  renderCard({ status: 'approved', dirty: true });

  // The badge lives on the preview card, but the edits happen here, so the
  // consequence has to be visible here too.
  expect(screen.getByText('Сохранение снимет утверждение')).toBeInTheDocument();
  expect(screen.queryByText('✓ Утверждён')).not.toBeInTheDocument();
});

test('an untouched approved scenario just says it is approved', () => {
  renderCard({ status: 'approved' });

  expect(screen.getByText('✓ Утверждён')).toBeInTheDocument();
});

test('a new message step inherits the first role and lands at the end', async () => {
  renderCard();

  await userEvent.click(screen.getByText('+ Реплика'));

  expect(screen.getByLabelText('Роль шага 3')).toHaveValue('r1');
  expect(screen.getByLabelText('Текст шага 3')).toHaveValue('');
});

test('a reaction step offers the eight emoji as one radio group', async () => {
  renderCard();

  await userEvent.click(screen.getByText('+ Реакция'));
  const group = screen.getByRole('radiogroup', { name: 'Реакция шага 3' });
  expect(group.querySelectorAll('[role="radio"]')).toHaveLength(8);
  // The first one is preselected, so a reaction step is never saved emoji-less.
  expect(screen.getByRole('radio', { name: '👍' })).toHaveAttribute('aria-checked', 'true');

  await userEvent.click(screen.getByRole('radio', { name: '🔥' }));
  expect(screen.getByRole('radio', { name: '🔥' })).toHaveAttribute('aria-checked', 'true');
  expect(screen.getByRole('radio', { name: '👍' })).toHaveAttribute('aria-checked', 'false');
});

test('a step may only point at a step above it', async () => {
  renderCard();
  await userEvent.click(screen.getByText('+ Реплика'));

  const select = screen.getByLabelText('Шаг 3 отвечает на');
  expect([...select.querySelectorAll('option')].map((option) => option.value)).toEqual([
    '',
    '1',
    '2',
  ]);
  // Step 1 has nothing above it at all.
  expect([...screen.getByLabelText('Шаг 1 отвечает на').querySelectorAll('option')]).toHaveLength(
    1,
  );
});

test('removing a step renumbers every link that pointed past it', async () => {
  renderCard({
    draft: {
      ...DRAFT,
      mediaMessageLink: 'https://t.me/c/1',
      mediaStepPosition: 3,
      steps: [
        step('s1'),
        step('s2'),
        step('s3', { replyToPosition: 2 }),
        step('s4', { replyToPosition: 3 }),
      ],
    },
  });

  await userEvent.click(screen.getByLabelText('Удалить шаг 2'));

  // The link AT the removed step is dropped; the one past it slides down.
  expect(screen.getByLabelText('Шаг 2 отвечает на')).toHaveValue('');
  expect(screen.getByLabelText('Шаг 3 отвечает на')).toHaveValue('2');
  // The media slot is a one-based position into the same list.
  expect(screen.getByLabelText('Шаг с медиа')).toHaveValue('2');
});

test('removing a role unassigns the steps that spoke as it', async () => {
  renderCard();

  await userEvent.click(screen.getByLabelText('Удалить роль 2'));

  expect(screen.getByLabelText('Роль шага 2')).toHaveValue('');
  expect(screen.queryByLabelText('Название роли 2')).not.toBeInTheDocument();
});

test('the delay pair cannot be inverted, in either direction', async () => {
  renderCard();

  // `delay_min > delay_max` is a 422 from the input schema — an unreadable
  // validation blob — so the pair moves together in both directions.
  await userEvent.clear(screen.getByLabelText('Минимальная пауза перед шагом 1'));
  await userEvent.type(screen.getByLabelText('Минимальная пауза перед шагом 1'), '300');
  expect(screen.getByLabelText('Максимальная пауза перед шагом 1')).toHaveValue(300);

  await userEvent.clear(screen.getByLabelText('Максимальная пауза перед шагом 1'));
  await userEvent.type(screen.getByLabelText('Максимальная пауза перед шагом 1'), '5');
  expect(screen.getByLabelText('Максимальная пауза перед шагом 1')).toHaveValue(5);
  expect(screen.getByLabelText('Минимальная пауза перед шагом 1')).toHaveValue(0);
});

test('a delay is held inside the column bounds', async () => {
  renderCard();

  await userEvent.clear(screen.getByLabelText('Максимальная пауза перед шагом 1'));
  await userEvent.type(screen.getByLabelText('Максимальная пауза перед шагом 1'), '99999');
  expect(screen.getByLabelText('Максимальная пауза перед шагом 1')).toHaveValue(3600);
});

test('saving is offered only for a dirty form whose roles are all named', async () => {
  const onSave = vi.fn();
  renderCard({ dirty: true, onSave });
  const save = screen.getByText('Использовать сценарий');
  expect(save).toBeEnabled();

  await userEvent.clear(screen.getByLabelText('Название роли 1'));
  expect(save).toBeDisabled();
  expect(screen.getByText('У каждой роли должно быть название')).toBeInTheDocument();

  await userEvent.type(screen.getByLabelText('Название роли 1'), 'Скептик');
  await userEvent.click(save);
  expect(onSave).toHaveBeenCalledTimes(1);
});

test('a pristine form has nothing to save', () => {
  renderCard();

  expect(screen.getByText('Использовать сценарий')).toBeDisabled();
});

test('approval waits for the edits to be saved', () => {
  renderCard({ dirty: true });

  // Approving validates what is STORED, so approving over unsaved edits would
  // vouch for the previous text.
  expect(screen.getByText('Утвердить')).toBeDisabled();
});

test('approval fires once the form matches the server', async () => {
  const onApprove = vi.fn();
  renderCard({ onApprove });

  await userEvent.click(screen.getByText('Утвердить'));
  expect(onApprove).toHaveBeenCalledTimes(1);
});

test('generation refuses an empty topic — the server would only refuse it later', () => {
  renderCard({ draft: { ...DRAFT, topic: '   ' } });

  expect(screen.getByText('Сгенерировать через ИИ')).toBeDisabled();
});

test('generation stands down while a request is in flight', () => {
  renderCard({ busy: true });

  expect(screen.getByText('Сгенерировать через ИИ')).toBeDisabled();
});

test('generation asks the page, which owns the overwrite confirmation', async () => {
  const onGenerate = vi.fn();
  renderCard({ onGenerate });

  await userEvent.click(screen.getByText('Сгенерировать через ИИ'));
  expect(onGenerate).toHaveBeenCalledTimes(1);
});

test('the persona stepper reports the step it took', async () => {
  const onPersonaCount = vi.fn();
  renderCard({ onPersonaCount });

  await userEvent.click(screen.getByLabelText('Ролей: на один больше'));
  expect(onPersonaCount).toHaveBeenCalledWith(4);
  await userEvent.click(screen.getByLabelText('Ролей: на один меньше'));
  expect(onPersonaCount).toHaveBeenCalledWith(2);
});

test('the campaign fields of the brief are editable and switch together', async () => {
  renderCard();

  await userEvent.click(screen.getByRole('radio', { name: 'Оживление чата' }));
  expect(screen.getByRole('radio', { name: 'Оживление чата' })).toHaveAttribute(
    'aria-checked',
    'true',
  );

  await userEvent.click(screen.getByRole('switch', { name: 'Читать чат перед репликой' }));
  expect(screen.getByRole('switch', { name: 'Читать чат перед репликой' })).toHaveAttribute(
    'aria-checked',
    'true',
  );

  await userEvent.type(screen.getByLabelText('Тема'), '!');
  expect(screen.getByLabelText('Тема')).toHaveValue('про сервис доставки!');
});

test('the media slot offers a message step by position', async () => {
  renderCard();

  await userEvent.type(screen.getByLabelText('Ссылка на сообщение с медиа'), 'https://t.me/c/1');
  await userEvent.selectOptions(screen.getByLabelText('Шаг с медиа'), '2');
  expect(screen.getByLabelText('Шаг с медиа')).toHaveValue('2');
});

test('the media slot skips a reaction step without renumbering the rest', async () => {
  renderCard({
    draft: {
      ...DRAFT,
      steps: [step('s1'), step('s2', { kind: 'reaction', emoji: '🔥' }), step('s3')],
    },
  });

  await userEvent.type(screen.getByLabelText('Ссылка на сообщение с медиа'), 'https://t.me/c/1');

  // The media rides along with the step's own send, so a reaction has nothing to
  // carry it and position 2 is not offered — the same filter the reaction target
  // picker applies. The third step stays "#3": a position is an index into the
  // whole list, so filtering must not renumber what is left.
  const options = within(screen.getByLabelText('Шаг с медиа')).getAllByRole('option');
  expect(options.map((option) => option.textContent)).toEqual(['Без медиа', '#1', '#3']);

  await userEvent.selectOptions(screen.getByLabelText('Шаг с медиа'), '3');
  expect(screen.getByLabelText('Шаг с медиа')).toHaveValue('3');
});

test('an empty scenario says so rather than showing an empty list', () => {
  renderCard({ draft: { ...DRAFT, roles: [], steps: [] } });

  expect(screen.getByText('Ролей пока нет')).toBeInTheDocument();
  expect(screen.getByText('Шагов пока нет')).toBeInTheDocument();
});

test('a new role starts empty and unnamed', async () => {
  renderCard({ draft: { ...DRAFT, roles: [], steps: [] } });

  await userEvent.click(screen.getByText('+ Добавить роль'));
  expect(screen.getByLabelText('Название роли 1')).toHaveValue('');
  expect(screen.getByText('У каждой роли должно быть название')).toBeInTheDocument();
});

test('a reply link is chosen by position and kept', async () => {
  renderCard();

  await userEvent.selectOptions(screen.getByLabelText('Шаг 2 отвечает на'), '1');
  expect(screen.getByLabelText('Шаг 2 отвечает на')).toHaveValue('1');

  await userEvent.selectOptions(screen.getByLabelText('Шаг 2 отвечает на'), '');
  expect(screen.getByLabelText('Шаг 2 отвечает на')).toHaveValue('');
});

test('a reaction points at its target through its own select', async () => {
  renderCard({
    draft: { ...DRAFT, steps: [step('s1'), step('s2', { kind: 'reaction', emoji: '🔥' })] },
  });

  await userEvent.selectOptions(screen.getByLabelText('Шаг 2 реагирует на'), '1');
  expect(screen.getByLabelText('Шаг 2 реагирует на')).toHaveValue('1');
});
