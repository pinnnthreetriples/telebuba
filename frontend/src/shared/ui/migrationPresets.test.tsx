import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import {
  Button,
  CollapsibleCard,
  Input,
  ModalBody,
  ModalFooter,
  ModalHeader,
  Textarea,
  StatTile,
  SettingRow,
} from './index';

test('embedded sections keep their original flush shell', () => {
  render(
    <CollapsibleCard
      appearance="embedded"
      headerPadding="none"
      bodyPadding="inset"
      header="Section"
      label="Toggle"
      defaultOpen
    >
      Content
    </CollapsibleCard>,
  );
  expect(screen.getByText('Section').parentElement).toHaveClass('px-0', 'py-0');
  expect(screen.getByText('Section').closest('.overflow-hidden')).not.toHaveClass('border');
  expect(screen.getByText('Content')).toHaveClass('px-0', 'pb-0', 'pt-md');
});
test('listener and history dialog slots preserve border and spacing decisions', () => {
  render(
    <>
      <ModalHeader variant="listener">Listener</ModalHeader>
      <ModalBody variant="history">History</ModalBody>
      <ModalFooter variant="listener">Actions</ModalFooter>
    </>,
  );
  expect(screen.getByText('Listener')).toHaveClass('px-xl', 'pt-xl', 'pb-lg');
  expect(screen.getByText('Listener')).not.toHaveClass('border-b');
  expect(screen.getByText('History')).toHaveClass('px-2xl', 'pt-md', 'pb-lg');
  expect(screen.getByText('Actions')).toHaveClass('px-xl', 'pb-xl');
  expect(screen.getByText('Actions')).not.toHaveClass('border-t');
});
test('numeric and authentication fields request named geometry', () => {
  render(
    <>
      <Input variant="readout" aria-label="Limit" />
      <Input variant="inlineCaption" aria-label="Range" />
      <Input variant="auth" aria-label="Code" />
    </>,
  );
  expect(screen.getByRole('textbox', { name: 'Limit' })).toHaveClass(
    'px-md',
    'py-tight',
    'font-mono',
    'font-semibold',
  );
  expect(screen.getByRole('textbox', { name: 'Range' })).toHaveClass(
    'border-none',
    'p-0',
    'type-caption',
  );
  expect(screen.getByRole('textbox', { name: 'Code' })).toHaveClass('px-md', 'py-md', 'rounded-lg');
});
test('prompt and chat composer inherit shared geometry without fixed heights', () => {
  render(
    <>
      <Textarea variant="prompt" aria-label="Prompt" />
      <Textarea variant="composer" aria-label="Message" />
    </>,
  );
  expect(screen.getByRole('textbox', { name: 'Prompt' })).toHaveClass('px-lg', 'py-md');
  expect(screen.getByRole('textbox', { name: 'Message' })).toHaveClass(
    'px-md',
    'py-sm',
    'min-h-control',
    'type-prose',
  );
});
test('responsive actions use central presentation variants', () => {
  render(
    <>
      <Button shape="square" variant="info">
        Create
      </Button>
      <Button presentation="multiline">Privacy</Button>
      <Button presentation="compactFooter">Save</Button>
    </>,
  );
  expect(screen.getByRole('button', { name: 'Create' })).toHaveClass('rounded-lg', 'bg-info-tint');
  expect(screen.getByRole('button', { name: 'Privacy' })).toHaveClass(
    'h-auto',
    'min-h-touch',
    'sm:h-field',
    'px-md',
    'sm:px-xl',
  );
  expect(screen.getByRole('button', { name: 'Save' })).toHaveClass('px-md', 'sm:px-2xl');
});
test('product statistic families keep their shared surface and inset decisions', () => {
  render(
    <>
      <StatTile variant="plain" label="Warming" value={3} />
      <StatTile variant="runtime" label="Comments" value={4} />
      <StatTile variant="launch" label="Accounts" value={5} />
      <SettingRow labelTone="body" label="Range">
        <Input />
      </SettingRow>
    </>,
  );
  expect(screen.getByText('Warming').parentElement).not.toHaveClass('border');
  expect(screen.getByText('Comments').parentElement).toHaveClass('px-lg', 'py-lg');
  expect(screen.getByText('Accounts').parentElement).toHaveClass('px-md', 'py-md');
  expect(screen.getByText('Accounts')).toHaveClass('mt-xs');
  expect(screen.getByText('Range')).toHaveClass('text-body');
});
