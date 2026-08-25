import { render } from '@testing-library/react';
import { expect, test } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { FeedbackMark } from './FeedbackMark';

test('renders nothing when there is no result', () => {
  const { container } = render(<FeedbackMark />);
  expect(container).toBeEmptyDOMElement();
});

test('renders a success mark', async () => {
  const { container } = render(<FeedbackMark result="ok" />);
  expect(container.querySelector('.text-success-deep svg')).toBeInTheDocument();
  await expectNoAxeViolations(container);
});

test('renders an error mark', () => {
  const { container } = render(<FeedbackMark result="err" />);
  expect(container.querySelector('.text-danger svg')).toBeInTheDocument();
});
