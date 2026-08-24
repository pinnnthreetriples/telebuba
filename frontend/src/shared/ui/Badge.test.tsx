import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import { Badge } from './Badge';
import { Notice } from './Notice';

test('the tone names the fill and the readable text rung together', () => {
  render(
    <>
      <Badge>7</Badge>
      <Badge tone="danger">3 удалено</Badge>
      <Badge tone="success">Готов</Badge>
    </>,
  );

  expect(screen.getByText('7').className).toContain('bg-track');
  expect(screen.getByText('7').className).toContain('text-ink-muted');

  // `text-danger` would be 4.34:1 on this fill; the pairing is what `deep` is for.
  const deleted = screen.getByText('3 удалено').className;
  expect(deleted).toContain('bg-danger-tint');
  expect(deleted).toContain('text-danger-deep');
  expect(deleted.split(' ')).not.toContain('text-danger');

  expect(screen.getByText('Готов').className).toContain('text-success-deep');
});

test('the size picks the chip or the label rung', () => {
  render(
    <>
      <Badge>10</Badge>
      <Badge size="md">Прогрев</Badge>
    </>,
  );

  expect(screen.getByText('10').className).toContain('text-micro');
  expect(screen.getByText('Прогрев').className).toContain('text-body');
});

test('a badge never wraps and a caller can still add layout', () => {
  render(<Badge className="ml-auto">2</Badge>);

  const classes = screen.getByText('2').className;
  expect(classes).toContain('whitespace-nowrap');
  expect(classes).toContain('ml-auto');
});

test('a notice carries its tone and drops the border on request', () => {
  render(
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
