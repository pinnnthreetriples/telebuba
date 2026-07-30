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
    render(<Harness telemetrConfigured initial={{ ...EMPTY_FORM, useTelemetr: true }} />);
    const [language, country] = screen.getAllByRole<HTMLSelectElement>('combobox');

    await userEvent.selectOptions(language!, 'ar');
    await userEvent.selectOptions(country!, 'AE');

    expect(language).toHaveValue('ar');
    expect(country).toHaveValue('AE');
  });

  it('disables language and country until the Telemetr source is in play', async () => {
    // Only the catalogue filters by locale, so with that source off the two selects
    // would silently narrow nothing.
    render(<Harness telemetrConfigured />);
    expect(screen.getAllByRole('combobox')[0]).toBeDisabled();
    expect(screen.getAllByRole('combobox')[1]).toBeDisabled();

    await userEvent.click(screen.getByRole('checkbox', { name: /Telemetr\.io/ }));

    expect(screen.getAllByRole('combobox')[0]).toBeEnabled();
    expect(screen.getAllByRole('combobox')[1]).toBeEnabled();
  });

  it('says the two selects only reach the catalogue', () => {
    render(<Harness />);
    // The scope has to be stated where the fields are, not only on the checkbox — and
    // it must not claim the subscriber bounds behave the same way.
    const hints = screen.getAllByRole('note', { name: /только каталог Telemetr\.io/ });
    expect(hints).toHaveLength(2);
    expect(hints[0]).toHaveAccessibleName(/С подписчиками иначе/);
  });

  it('explains the Telemetr source without switching it on', async () => {
    // On a phone a tap is the only way to open a hover hint; nested in the label it
    // activated the checkbox instead.
    render(<Harness telemetrConfigured />);
    const checkbox = screen.getByRole('checkbox', { name: /Telemetr\.io/ });

    await userEvent.click(screen.getByRole('note', { name: /Расходует квоту/ }));

    expect(checkbox).not.toBeChecked();
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
