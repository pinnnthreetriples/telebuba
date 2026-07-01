import { render } from '@testing-library/react';
import { expect, test } from 'vitest';

import { FeedbackMark } from './FeedbackMark';

test('renders nothing when there is no result', () => {
  const { container } = render(<FeedbackMark />);
  expect(container).toBeEmptyDOMElement();
});

test('renders a success mark', () => {
  const { container } = render(<FeedbackMark result="ok" />);
  expect(container.querySelector('.text-success svg')).toBeInTheDocument();
});

test('renders an error mark', () => {
  const { container } = render(<FeedbackMark result="err" />);
  expect(container.querySelector('.text-danger svg')).toBeInTheDocument();
});
