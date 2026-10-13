import { fireEvent, render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import { Slider } from './Slider';

test('reports the new number and announces the value text', () => {
  const onChange = vi.fn();
  render(
    <Slider
      value={120}
      min={10}
      max={600}
      step={10}
      onChange={onChange}
      label="Пауза"
      valueText="1–3 мин"
    />,
  );
  const slider = screen.getByRole('slider', { name: 'Пауза' });

  expect(slider).toHaveAttribute('aria-valuetext', '1–3 мин');
  fireEvent.change(slider, { target: { value: '300' } });
  expect(onChange).toHaveBeenCalledWith(300);
});

test('a disabled slider is not operable', () => {
  render(<Slider value={5} min={1} max={20} onChange={() => {}} label="Номеров" disabled />);
  expect(screen.getByRole('slider', { name: 'Номеров' })).toBeDisabled();
});
