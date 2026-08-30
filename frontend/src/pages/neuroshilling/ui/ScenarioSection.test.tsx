import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { NeuroshillingBoardAccount } from '@/shared/api';

// Ростер, из которого карточка роли выбирает исполнителя.
const POOL: NeuroshillingBoardAccount[] = [
  { account_id: 'a1', title: 'Алиса', assigned: true },
  { account_id: 'a2', title: 'Борис', assigned: true },
];

import { ScenarioSection } from './ScenarioSection';
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
  onApprove?: () => void;
  pool?: NeuroshillingBoardAccount[];
  onAssignRole?: (roleId: string, accountId: string | null) => void;
}

// The card is controlled, so the harness owns the draft the way the page does —
// otherwise every edit would be discarded and nothing below could be observed.
function Harness({ options }: { options: Options }) {
  const [draft, setDraft] = useState(options.draft ?? DRAFT);
  return (
    <ScenarioSection
      draft={draft}
      onDraft={setDraft}
      status={options.status ?? 'draft'}
      dirty={options.dirty ?? false}
      onGenerate={options.onGenerate ?? (() => undefined)}
      onApprove={options.onApprove ?? (() => undefined)}
      pool={options.pool ?? POOL}
      onAssignRole={options.onAssignRole ?? (() => undefined)}
      busy={options.busy ?? false}
    />
  );
}

function renderCard(options: Options = {}) {
  return render(<Harness options={options} />);
}

// Every Select keeps its own option list in the DOM (collapsed and inert), and this
// card has one per step, so both helpers scope to the list the named trigger owns
// instead of to the whole card.
function panelOf(label: string): HTMLElement {
  return screen.getByLabelText(label).parentElement!;
}

function optionLabels(label: string): (string | null)[] {
  return within(panelOf(label))
    .getAllByRole('option')
    .map((option) => option.textContent);
}

async function pick(label: string, option: string) {
  await userEvent.click(screen.getByLabelText(label));
  await userEvent.click(within(panelOf(label)).getByRole('option', { name: option }));
}

test('the roles and the dialogue are laid out in order', () => {
  renderCard();

  expect(screen.getByLabelText('Название роли 1')).toHaveValue('Скептик');
  expect(screen.getByLabelText('Персона роли 2')).toHaveValue('делится опытом');
  expect(screen.getByLabelText('Текст шага 1')).toHaveValue('текст s1');
  expect(screen.getByLabelText('Роль шага 2')).toHaveTextContent('Клиент');
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

  expect(screen.getByLabelText('Роль шага 3')).toHaveTextContent('Скептик');
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

  expect(optionLabels('Шаг 3 отвечает на')).toEqual(['—', '#1', '#2']);
  // Step 1 has nothing above it at all.
  expect(optionLabels('Шаг 1 отвечает на')).toEqual(['—']);
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
  expect(screen.getByLabelText('Шаг 2 отвечает на')).toHaveTextContent('—');
  expect(screen.getByLabelText('Шаг 3 отвечает на')).toHaveTextContent('#2');
  // The media slot is a one-based position into the same list.
  await userEvent.click(screen.getByRole('button', { name: 'Вложение' }));
  expect(screen.getByLabelText('Шаг с медиа')).toHaveTextContent('#2');
});

test('a step can change kind, and its link travels under the right name', async () => {
  // `replyToPosition` читает только сообщение, `targetPosition` — только реакция, и
  // позиция, оставшаяся под именем другого вида, отвергается сервером
  // (`scenario_invalid`), а прогоном просто не читается.
  renderCard({
    draft: { ...DRAFT, steps: [step('s1'), step('s2', { replyToPosition: 1 })] },
  });

  // Обе реплики предлагают одно и то же — берётся кнопка ВТОРОГО шага.
  await userEvent.click(screen.getAllByRole('button', { name: 'Сделать реакцией' })[1]!);

  // Реакция без эмодзи — шаг, который прогон молча пропустит, поэтому оно проставлено.
  expect(screen.getByRole('radiogroup', { name: 'Реакция шага 2' })).toBeInTheDocument();
  expect(screen.getByLabelText('Шаг 2 реагирует на')).toHaveTextContent('#1');
  expect(screen.queryByLabelText('Текст шага 2')).toBeNull();

  await userEvent.click(screen.getByRole('button', { name: 'Сделать репликой' }));
  expect(screen.getByLabelText('Текст шага 2')).toBeInTheDocument();
  expect(screen.getByLabelText('Шаг 2 отвечает на')).toHaveTextContent('#1');
});

test('removing a role unassigns the steps that spoke as it', async () => {
  renderCard();

  await userEvent.click(screen.getByLabelText('Удалить роль 2'));

  expect(screen.getByLabelText('Роль шага 2')).toHaveTextContent('Выберите роль');
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
  // Минимум едет ЗА максимумом до 5, а не до нуля: опустошённое поле больше не
  // засчитывается за ноль, поэтому пара зажимается по набранному значению.
  expect(screen.getByLabelText('Минимальная пауза перед шагом 1')).toHaveValue(5);
});

test('an emptied delay accepts the number typed next, instead of gluing it to a zero', async () => {
  // `Number('')` — ноль, поэтому управляемое поле с зажимом на каждом нажатии переписывало
  // стёртое значение нулём, и «60» → «180» давало «0180».
  renderCard();
  const min = screen.getByLabelText('Минимальная пауза перед шагом 1');
  expect(min).toHaveValue(60);

  await userEvent.clear(min);
  expect(min).toHaveValue(null);

  await userEvent.type(min, '180');
  expect(min).toHaveValue(180);
});

test('a delay left empty comes back to its stored value rather than to zero', async () => {
  renderCard();
  const min = screen.getByLabelText('Минимальная пауза перед шагом 1');

  await userEvent.clear(min);
  await userEvent.tab();
  expect(min).toHaveValue(60);
});

test('a delay is held inside the column bounds', async () => {
  renderCard();

  await userEvent.clear(screen.getByLabelText('Максимальная пауза перед шагом 1'));
  await userEvent.type(screen.getByLabelText('Максимальная пауза перед шагом 1'), '99999');
  expect(screen.getByLabelText('Максимальная пауза перед шагом 1')).toHaveValue(3600);
});

test('a nameless role blocks approval and says so', async () => {
  // Сохранение уехало в подвал диалога настроек, но проверка имени осталась ЗДЕСЬ и
  // держит ближайшую к себе кнопку: утверждать сценарий с безымянной ролью нельзя.
  renderCard();
  await userEvent.clear(screen.getByLabelText('Название роли 1'));
  expect(screen.getByText('Превью сценария')).toBeDisabled();
  expect(screen.getByText('У каждой роли должно быть название')).toBeInTheDocument();
});

test('approval waits for the edits to be saved', () => {
  renderCard({ dirty: true });

  // Approving validates what is STORED, so approving over unsaved edits would
  // vouch for the previous text.
  expect(screen.getByText('Превью сценария')).toBeDisabled();
});

test('approval fires once the form matches the server', async () => {
  const onApprove = vi.fn();
  renderCard({ onApprove });

  await userEvent.click(screen.getByText('Превью сценария'));
  expect(onApprove).toHaveBeenCalledTimes(1);
});

test('generation refuses an empty topic — the server would only refuse it later', () => {
  renderCard({ draft: { ...DRAFT, topic: '   ' } });

  expect(screen.getByRole('button', { name: 'Сгенерировать через ИИ' })).toBeDisabled();
});

test('generation stands down while a request is in flight', () => {
  renderCard({ busy: true });

  expect(screen.getByRole('button', { name: 'Сгенерировать через ИИ' })).toBeDisabled();
});

test('generation asks the page, which owns the overwrite confirmation', async () => {
  const onGenerate = vi.fn();
  renderCard({ onGenerate });

  await userEvent.click(screen.getByRole('button', { name: 'Сгенерировать через ИИ' }));
  expect(onGenerate).toHaveBeenCalledTimes(1);
});

test('the media slot offers a message step by position', async () => {
  renderCard();
  // Слот медиа раскрывается скрепкой.
  await userEvent.click(screen.getByRole('button', { name: 'Вложение' }));

  await userEvent.type(screen.getByLabelText('Ссылка на сообщение с медиа'), 'https://t.me/c/1');
  await pick('Шаг с медиа', '#2');
  expect(screen.getByLabelText('Шаг с медиа')).toHaveTextContent('#2');
});

test('the media slot skips a reaction step without renumbering the rest', async () => {
  renderCard({
    draft: {
      ...DRAFT,
      steps: [step('s1'), step('s2', { kind: 'reaction', emoji: '🔥' }), step('s3')],
    },
  });
  // Слот медиа раскрывается скрепкой.
  await userEvent.click(screen.getByRole('button', { name: 'Вложение' }));

  await userEvent.type(screen.getByLabelText('Ссылка на сообщение с медиа'), 'https://t.me/c/1');

  // The media rides along with the step's own send, so a reaction has nothing to
  // carry it and position 2 is not offered — the same filter the reaction target
  // picker applies. The third step stays "#3": a position is an index into the
  // whole list, so filtering must not renumber what is left.
  expect(optionLabels('Шаг с медиа')).toEqual(['Без медиа', '#1', '#3']);

  await pick('Шаг с медиа', '#3');
  expect(screen.getByLabelText('Шаг с медиа')).toHaveTextContent('#3');
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

  await pick('Шаг 2 отвечает на', '#1');
  expect(screen.getByLabelText('Шаг 2 отвечает на')).toHaveTextContent('#1');

  await pick('Шаг 2 отвечает на', '—');
  expect(screen.getByLabelText('Шаг 2 отвечает на')).toHaveTextContent('—');
});

test('a reaction points at its target through its own select', async () => {
  renderCard({
    draft: { ...DRAFT, steps: [step('s1'), step('s2', { kind: 'reaction', emoji: '🔥' })] },
  });

  await pick('Шаг 2 реагирует на', '#1');
  expect(screen.getByLabelText('Шаг 2 реагирует на')).toHaveTextContent('#1');
});
