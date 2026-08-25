import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { Badge } from './Badge';
import { Notice } from './Notice';

test('the tone names the fill and the readable text rung together', async () => {
  const { container } = render(
    <>
      <Badge>7</Badge>
      <Badge tone="danger">3 удалено</Badge>
      <Badge tone="success">Готов</Badge>
    </>,
  );

  expect(screen.getByText('7').className).toContain('bg-canvas');
  expect(screen.getByText('7').className).toContain('text-ink-muted');

  // `text-danger` would be 4.34:1 on this fill; the pairing is what `deep` is for.
  const deleted = screen.getByText('3 удалено').className;
  expect(deleted).toContain('bg-danger-tint');
  expect(deleted).toContain('text-danger-deep');
  expect(deleted.split(' ')).not.toContain('text-danger');

  expect(screen.getByText('Готов').className).toContain('text-success-deep');
  await expectNoAxeViolations(container);
});

test('the size picks one of the three pill rungs', () => {
  render(
    <>
      <Badge>10</Badge>
      <Badge size="sm">Забанен</Badge>
      <Badge size="md">Прогрев</Badge>
    </>,
  );

  expect(screen.getByText('10').className).toContain('text-micro');
  // The rung every status pill in the app sits on, and the one this component
  // could not express until it was added.
  expect(screen.getByText('Забанен').className).toContain('text-tiny');
  expect(screen.getByText('Прогрев').className).toContain('text-body');
});

test('the dot is asked for, and cannot disagree with the label it sits beside', () => {
  render(
    <>
      <Badge tone="danger" dot>
        Забанен
      </Badge>
      <Badge tone="danger">3 удалено</Badge>
    </>,
  );

  expect(screen.getByText('Забанен').querySelector('.bg-current')).toBeInTheDocument();
  expect(screen.getByText('3 удалено').querySelector('.bg-current')).toBeNull();
});

test('a badge never wraps and a caller can still add layout', () => {
  render(<Badge className="ml-auto">2</Badge>);

  const classes = screen.getByText('2').className;
  expect(classes).toContain('whitespace-nowrap');
  expect(classes).toContain('ml-auto');
});

// Notice has no test file of its own, so its axe pass lives here beside Badge's.
test('a notice carries its tone and drops the border on request', async () => {
  const { container } = render(
    <>
      <Notice tone="danger">Не удалось</Notice>
      <Notice tone="warning" bordered={false}>
        Осторожно
      </Notice>
    </>,
  );

  const failed = screen.getByText('Не удалось').className;
  expect(failed).toContain('bg-danger-tint');
  expect(failed).toContain('border-danger-line');
  expect(failed).toContain('text-danger-deep');

  const careful = screen.getByText('Осторожно').className;
  expect(careful).toContain('bg-warning-tint');
  expect(careful.split(' ')).not.toContain('border');
  await expectNoAxeViolations(container);
});

// A notice is prose that was already on screen; only the ones reporting an outcome
// announce themselves, and they say so at the call site.
test('a notice is not a live region unless the caller makes it one', () => {
  render(
    <>
      <Notice>Подсказка</Notice>
      <Notice role="alert">Ошибка</Notice>
    </>,
  );

  expect(screen.getByText('Подсказка')).not.toHaveAttribute('role');
  expect(screen.getByRole('alert')).toHaveTextContent('Ошибка');
});
