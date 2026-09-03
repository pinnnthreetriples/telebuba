import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import '@/shared/i18n';

import { expectNoAxeViolations } from '@/shared/ui/axe.test-helpers';

import { EMPTY_FORM, type DiscoveryFormState } from '../model/discovery';
import { ACCESS, COMMENTS, KINDS } from '../model/filters';
import { DiscoveryFilters } from './DiscoveryFilters';

const t = (key: string) => {
  const ru: Record<string, string> = {
    'kind.all': 'Все',
    'kind.channels': 'Каналы',
    'kind.groups': 'Группы',
    'comments.any': 'Любые',
    'comments.on': 'Есть',
    'comments.off': 'Нет',
    'access.any': 'Любой',
    'access.open': 'Открытый',
    'access.join_request': 'По заявке',
    'access.subscription': 'Подписка',
  };
  return ru[key] ?? key;
};

function Harness({
  initial = EMPTY_FORM,
  onForm,
}: {
  initial?: DiscoveryFormState;
  onForm: (form: DiscoveryFormState) => void;
}) {
  const [form, setForm] = useState(initial);
  return (
    <DiscoveryFilters
      form={form}
      onChange={(next) => {
        setForm(next);
        onForm(next);
      }}
    />
  );
}

function renderFilters(initial?: DiscoveryFormState) {
  const onForm = vi.fn<(form: DiscoveryFormState) => void>();
  const view = render(<Harness initial={initial} onForm={onForm} />);
  const last = () => onForm.mock.lastCall?.[0];
  return { ...view, last };
}

const radio = (name: string) => screen.getByRole('radio', { name });

describe('DiscoveryFilters', () => {
  it.each(KINDS)('picks kind %s', async (kind) => {
    const { last } = renderFilters({ ...EMPTY_FORM, kind: 'all' });
    await userEvent.click(radio(t(`kind.${kind}`)));
    expect(last()?.kind).toBe(kind);
    expect(radio(t(`kind.${kind}`))).toBeChecked();
  });

  it.each(COMMENTS)('picks comments %s', async (comments) => {
    const { last } = renderFilters({ ...EMPTY_FORM, comments: 'off' });
    await userEvent.click(radio(t(`comments.${comments}`)));
    expect(last()?.comments).toBe(comments);
  });

  it.each(ACCESS)('picks access %s', async (access) => {
    const { last } = renderFilters({ ...EMPTY_FORM, access: 'open' });
    await userEvent.click(radio(t(`access.${access}`)));
    expect(last()?.access).toBe(access);
  });

  it('picks a category and a language from their lists', async () => {
    const { last } = renderFilters();

    await userEvent.click(screen.getByRole('combobox', { name: 'Категория' }));
    await userEvent.click(screen.getByRole('option', { name: 'Крипта' }));
    expect(last()?.category).toBe('crypto');

    await userEvent.click(screen.getByRole('combobox', { name: 'Язык' }));
    await userEvent.click(screen.getByRole('option', { name: 'Українська' }));
    expect(last()?.language).toBe('uk');
  });

  it('switching to groups disables comments and subscription and normalises the state', async () => {
    const { last } = renderFilters({ ...EMPTY_FORM, comments: 'on', access: 'subscription' });

    await userEvent.click(radio('Группы'));

    // The server 422s both, so the state is normalised in the same change — the UI
    // never shows a disabled option as the selected one.
    expect(last()).toMatchObject({ kind: 'groups', comments: 'any', access: 'any' });
    for (const comments of COMMENTS) expect(radio(t(`comments.${comments}`))).toBeDisabled();
    expect(radio('Подписка')).toBeDisabled();
    expect(radio('Подписка')).toHaveAttribute('title', expect.stringMatching(/Подписочные/));
    expect(radio('Открытый')).toBeEnabled();
    expect(screen.getByText('Для групп нет вердикта по комментариям')).toBeInTheDocument();
  });

  it('keeps comments and subscription live for channels', () => {
    renderFilters();
    expect(radio('Есть')).toBeEnabled();
    expect(radio('Подписка')).toBeEnabled();
    expect(screen.queryByText('Для групп нет вердикта по комментариям')).not.toBeInTheDocument();
  });

  it('says that groups pass a comments filter when the kind is "all"', async () => {
    const { last } = renderFilters({ ...EMPTY_FORM, kind: 'all', comments: 'on' });
    const hint = 'Группы не фильтруются по комментариям';
    expect(screen.getByText(hint)).toBeInTheDocument();

    // Not with the filter off, and not for channels alone — nothing passes unfiltered there.
    await userEvent.click(radio('Любые'));
    expect(screen.queryByText(hint)).not.toBeInTheDocument();
    await userEvent.click(radio('Есть'));
    await userEvent.click(radio('Каналы'));
    expect(last()?.comments).toBe('on');
    expect(screen.queryByText(hint)).not.toBeInTheDocument();
  });

  it('toggles previously shown channels both ways', async () => {
    const { last } = renderFilters();
    expect(radio('Скрыть')).toBeChecked();

    await userEvent.click(radio('Показать'));
    expect(last()?.hideSeen).toBe(false);

    await userEvent.click(radio('Скрыть'));
    expect(last()?.hideSeen).toBe(true);
  });

  it('flags a limit outside the bounds instead of clamping it', async () => {
    const { last } = renderFilters();
    const limit = screen.getByRole('textbox', { name: 'Лимит результатов' });
    expect(limit).toHaveAttribute('placeholder', '200');

    await userEvent.type(limit, '600');

    expect(last()?.limit).toBe('600');
    expect(limit).toHaveAttribute('aria-invalid', 'true');
    // Announced, and read back as the field's own description — a red line under an
    // unnamed field is a colour carrying the meaning.
    expect(screen.getByText('Целое число от 1 до 500')).toHaveAttribute('role', 'status');
    expect(limit).toHaveAccessibleDescription('Целое число от 1 до 500');

    await userEvent.clear(limit);
    expect(limit).not.toHaveAttribute('aria-invalid');
    expect(screen.queryByText('Целое число от 1 до 500')).not.toBeInTheDocument();
  });

  it('keeps every message region in the DOM before its fault exists', async () => {
    // A live region announces a CHANGE to its content; one that mounts together with the
    // fault is silent. So the three (bounds, limit, seed) are static and empty, out of
    // flow while empty — never display:none, which drops them from the accessibility tree.
    renderFilters();
    const regions = screen.getAllByRole('status');
    expect(regions).toHaveLength(3);
    for (const region of regions) {
      expect(region).toBeEmptyDOMElement();
      expect(region).toHaveClass('empty:sr-only');
      expect(region).not.toHaveClass('hidden');
    }

    await userEvent.type(screen.getByRole('textbox', { name: 'Лимит результатов' }), '600');

    // The same element, now with text — not a new one.
    expect(screen.getAllByRole('status')).toHaveLength(3);
    expect(regions).toContain(screen.getByText('Целое число от 1 до 500'));
  });

  it('marks both subscriber bounds when they are the wrong way round', () => {
    renderFilters({ ...EMPTY_FORM, minSubscribers: '900', maxSubscribers: '100' });

    const min = screen.getByRole('textbox', { name: 'Подписчиков от' });
    const max = screen.getByRole('textbox', { name: 'Подписчиков до' });
    expect(min).toHaveAttribute('aria-invalid', 'true');
    expect(max).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText(/«Подписчиков от» больше/)).toHaveAttribute('role', 'status');
    expect(min).toHaveAccessibleDescription(/«Подписчиков от» больше/);
    expect(max).toHaveAccessibleDescription(/«Подписчиков от» больше/);
  });

  it('refuses a seed that is not a handle, naming the rule', async () => {
    // The API caps seed_channel at 32 and resolves a post link to nothing; without this
    // the Search button just went dead.
    renderFilters();
    const seed = screen.getByRole('textbox', { name: 'Похожие на канал' });
    const rule = 'Хэндл канала до 32 символов, без ссылок на посты';

    await userEvent.type(seed, 'https://t.me/durov/123');
    expect(seed).toHaveAttribute('aria-invalid', 'true');
    expect(seed).toHaveAccessibleDescription(rule);
    expect(screen.getByText(rule)).toHaveAttribute('role', 'status');

    // The web-preview link form is a handle once stripped.
    await userEvent.clear(seed);
    await userEvent.type(seed, 'https://t.me/s/durov');
    expect(seed).not.toHaveAttribute('aria-invalid');
    expect(screen.queryByText(rule)).not.toBeInTheDocument();

    await userEvent.clear(seed);
    await userEvent.type(seed, 'k'.repeat(33));
    expect(seed).toHaveAttribute('aria-invalid', 'true');
  });

  it('refuses a subscriber bound that is not a whole number instead of dropping it', async () => {
    // A `type="number"` field reported '' for "1e3", so the garbage became "no bound"
    // and the search ran unfiltered without a word.
    renderFilters();
    const min = screen.getByRole('textbox', { name: 'Подписчиков от' });
    expect(min).toHaveAttribute('inputmode', 'numeric');

    await userEvent.type(min, '1e3');

    expect(min).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('textbox', { name: 'Подписчиков до' })).not.toHaveAttribute(
      'aria-invalid',
    );
    expect(min).toHaveAccessibleDescription('Целое число от 0');

    await userEvent.clear(min);
    expect(min).not.toHaveAttribute('aria-invalid');
    expect(screen.queryByText('Целое число от 0')).not.toBeInTheDocument();
  });

  it('writes the subscriber bounds and the seed channel', async () => {
    const { last } = renderFilters();

    await userEvent.type(screen.getByRole('textbox', { name: 'Подписчиков от' }), '10');
    expect(last()?.minSubscribers).toBe('10');
    await userEvent.type(screen.getByRole('textbox', { name: 'Подписчиков до' }), '50');
    expect(last()?.maxSubscribers).toBe('50');

    // The hint sits outside the label, so its prose stays out of the field's name.
    await userEvent.type(screen.getByRole('textbox', { name: 'Похожие на канал' }), 'durov');
    expect(last()?.seedChannel).toBe('durov');
  });

  it('has no axe violations', async () => {
    const { container } = renderFilters({ ...EMPTY_FORM, kind: 'groups', limit: '0' });
    await expectNoAxeViolations(container);
  });
});
