import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import '@/shared/i18n';

import { EMPTY_FORM, type DiscoveryFormState } from '../model/discovery';
import { DiscoveryForm } from './DiscoveryForm';

function Harness({
  telemetrConfigured = false,
  onSubmit = vi.fn(),
  initial = EMPTY_FORM,
}: {
  telemetrConfigured?: boolean;
  onSubmit?: () => void;
  initial?: DiscoveryFormState;
}) {
  const [form, setForm] = useState(initial);
  return (
    <DiscoveryForm
      form={form}
      telemetrConfigured={telemetrConfigured}
      submitting={false}
      onChange={setForm}
      onSubmit={onSubmit}
    />
  );
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

  it('keeps the Telemetr toggle disabled until a key is configured', () => {
    const { unmount } = render(<Harness telemetrConfigured={false} />);
    expect(screen.getByRole('checkbox', { name: /Telemetr\.io/ })).toBeDisabled();
    unmount();

    render(<Harness telemetrConfigured />);
    expect(screen.getByRole('checkbox', { name: /Telemetr\.io/ })).toBeEnabled();
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

  it('defaults language and country to "any"', () => {
    render(<Harness />);
    const selects = screen.getAllByRole('combobox');
    expect(selects[0]).toHaveValue('');
    expect(selects[1]).toHaveValue('');
  });

  it('offers the targeted regions in both selects', async () => {
    render(<Harness />);
    const [language, country] = screen.getAllByRole<HTMLSelectElement>('combobox');

    await userEvent.selectOptions(language!, 'ar');
    await userEvent.selectOptions(country!, 'AE');

    expect(language).toHaveValue('ar');
    expect(country).toHaveValue('AE');
  });

  it('resets every field', async () => {
    render(<Harness initial={{ ...EMPTY_FORM, keywords: 'crypto', minSubscribers: '500' }} />);

    await userEvent.click(screen.getByRole('button', { name: 'Сбросить' }));

    expect(screen.getByPlaceholderText('крипта, трейдинг, новости')).toHaveValue('');
    expect(submitButton()).toBeDisabled();
  });
});
