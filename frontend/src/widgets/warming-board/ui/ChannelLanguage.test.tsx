import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { ChannelLanguageShortages } from './ChannelLanguageShortages';
import { WarmingChannelPill } from './WarmingChannelPill';

const CHANNEL = { channel: 'noticias', created_at: '2026-10-01T00:00:00+00:00' };

test('the shortage warning names the language, its channels and its accounts', () => {
  render(
    <ChannelLanguageShortages
      shortages={[
        { language: 'es', channels: 2, accounts: 7 },
        { language: 'pt', channels: 0, accounts: 1 },
      ]}
    />,
  );

  expect(screen.getByText('для es: 2 канала на 7 аккаунтов')).toBeInTheDocument();
  expect(screen.getByText('для pt: 0 каналов на 1 аккаунт')).toBeInTheDocument();
});

test('no shortage renders nothing', () => {
  const { container } = render(<ChannelLanguageShortages shortages={[]} />);

  expect(container).toBeEmptyDOMElement();
});

test('a labelled pill shows its language and country', () => {
  render(
    <WarmingChannelPill
      channel={{ ...CHANNEL, language: 'es', country: 'MX' }}
      onRemove={() => undefined}
    />,
  );

  expect(screen.getByText('noticias')).toBeInTheDocument();
  expect(screen.getByText('ES · MX')).toBeInTheDocument();
});

test('an unlabelled pill shows no tag and still removes its channel', async () => {
  const onRemove = vi.fn();
  render(<WarmingChannelPill channel={CHANNEL} onRemove={onRemove} />);

  expect(screen.queryByTitle('Язык и страна канала')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Удалить канал noticias' }));

  expect(onRemove).toHaveBeenCalledWith('noticias');
});
