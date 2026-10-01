import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import { i18n } from '@/shared/i18n';

import { MediaModal } from './MediaModal';
import type { ScenarioDraft } from './scenarioDraft';

const draft: ScenarioDraft = {
  campaignId: 'preview',
  mode: 'campaign',
  topic: '',
  uniqueMessages: false,
  useChatContext: false,
  mediaMessageLink: '',
  mediaStepPosition: null,
  roles: [],
  steps: [],
};

test('the media title and explanation remain stacked inside the shared header', () => {
  render(<MediaModal draft={draft} onDraft={vi.fn()} onClose={vi.fn()} />);
  const title = screen.getByText(i18n.t('neuroshilling.scenario.media.toggle'));
  const hint = screen.getByText(i18n.t('neuroshilling.scenario.media.hint'));
  expect(title.parentElement).toBe(hint.parentElement);
  expect(title.parentElement).not.toHaveClass('flex');
  expect(title.parentElement?.parentElement).toHaveClass('border-b');
});
