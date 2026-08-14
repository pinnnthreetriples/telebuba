import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import '@/shared/i18n';

import { EMPTY_FORM, type DiscoveryFormState } from '../model/discovery';
import { DiscoveryForm } from './DiscoveryForm';

function Harness({
  onSubmit = vi.fn(),
  initial = EMPTY_FORM,
}: {
  onSubmit?: () => void;
  initial?: DiscoveryFormState;
}) {
  const [form, setForm] = useState(initial);
  return <DiscoveryForm form={form} submitting={false} onChange={setForm} onSubmit={onSubmit} />;
}

const submitButton = () => screen.getByRole('button', { name: 'Найти' });

describe('DiscoveryForm', () => {
  it('disables submit until a long-enough keyword is typed', async () => {
    render(<Harness />);
    expect(submitButton()).toBeDisabled();

    await userEvent.type(screen.getByRole('textbox', { name: /Ключевые слова|крипта/i }), 'abc');
    expect(submitButton()).toBeDisabled();

    await userEvent.type(screen.getByRole('textbox', { name: /Ключевые слова|крипта/i }), 'd');
    expect(submitButton()).toBeEnabled();
  });

  it('reports how many keywords were parsed', async () => {
    render(<Harness />);
    const input = screen.getByPlaceholderText('крипта, трейдинг, новости');

    await userEvent.type(input, 'crypto, trading, ab');

    // 'ab' is below the minimum, so only two are counted.
    expect(screen.getByText(/Распознано: 2/)).toBeInTheDocument();
  });

  it('submits on the button and on Enter', async () => {
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} initial={{ ...EMPTY_FORM, keywords: 'crypto' }} />);

    await userEvent.click(submitButton());
    expect(onSubmit).toHaveBeenCalledTimes(1);

    await userEvent.type(screen.getByPlaceholderText('крипта, трейдинг, новости'), '{Enter}');
    expect(onSubmit).toHaveBeenCalledTimes(2);
  });

  it('does not submit an empty form on Enter', async () => {
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);

    await userEvent.type(screen.getByPlaceholderText('крипта, трейдинг, новости'), '{Enter}');

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('keeps the hint prose out of the seed field name', () => {
    render(<Harness />);
    // Nested in the label, the tooltip text joined the input's accessible name.
    expect(screen.getByRole('textbox', { name: 'Похожие на канал' })).toBeInTheDocument();
  });

  it('names the tokens it dropped', async () => {
    render(<Harness />);

    await userEvent.type(screen.getByPlaceholderText('крипта, трейдинг, новости'), 'crypto ab');

    expect(screen.getByText(/Пропущено: ab/)).toBeInTheDocument();
  });

  it('explains subscriber bounds the wrong way round instead of going dead', () => {
    render(
      <Harness
        initial={{
          ...EMPTY_FORM,
          keywords: 'crypto',
          minSubscribers: '900',
          maxSubscribers: '100',
        }}
      />,
    );

    expect(screen.getByText(/«Подписчиков от» больше/)).toBeInTheDocument();
    expect(submitButton()).toBeDisabled();
  });

  it('resets every field', async () => {
    render(<Harness initial={{ ...EMPTY_FORM, keywords: 'crypto', minSubscribers: '500' }} />);

    await userEvent.click(screen.getByRole('button', { name: 'Сбросить' }));

    expect(screen.getByPlaceholderText('крипта, трейдинг, новости')).toHaveValue('');
    expect(submitButton()).toBeDisabled();
  });
});
