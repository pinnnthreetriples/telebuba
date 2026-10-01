import { fireEvent, render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { componentSettings as settings, layoutSettings } from '@/shared/design-system';
import {
  Badge,
  Notice,
  TabList,
  SegmentedControl,
  Select,
  SelectableCard,
  Switch,
  HelpHint,
  Spinner,
  Modal,
  Input,
  Textarea,
  StatTile,
  ModalHeader,
  DataTable,
} from './index';

// Changing a named central setting must change actual consumers on the next render.
// Restore every setting even when an assertion fails so tests cannot mask each other.
function change<T extends object, K extends keyof T>(
  object: T,
  key: K,
  value: T[K],
  check: () => void,
) {
  const previous = object[key];
  try {
    object[key] = value;
    check();
  } finally {
    object[key] = previous;
  }
}

test('badge spacing propagates to multiple labels and preserves typed product variants', () => {
  const view = render(
    <>
      <Badge>One</Badge>
      <Badge appearance="channel" bordered contentGap="roomy" emphasis="bold">
        Two
      </Badge>
    </>,
  );
  expect(screen.getByText('One')).toHaveClass('gap-tight', 'font-medium');
  expect(screen.getByText('Two')).toHaveClass(
    'gap-sm',
    'font-bold',
    'border-line',
    'text-content-secondary',
  );
  change(settings.badge.padding.xs, 'x', 'lg', () => {
    view.rerender(
      <>
        <Badge>One</Badge>
        <Badge>Two</Badge>
      </>,
    );
    for (const label of ['One', 'Two']) expect(screen.getByText(label)).toHaveClass('px-lg');
    expect(screen.getByText('One')).not.toHaveClass('px-sm');
  });
});

test('notice compact and caption modes follow central padding without conflicting defaults', () => {
  const view = render(
    <Notice padding="compact" typography="caption" contentGap="row">
      Notice
    </Notice>,
  );
  expect(screen.getByText('Notice')).toHaveClass('py-sm', 'text-tiny', 'font-medium', 'gap-md');
  expect(screen.getByText('Notice')).not.toHaveClass('py-md', 'text-body');
  change(settings.notice.padding.compact, 'y', 'lg', () => {
    view.rerender(<Notice padding="compact">Notice</Notice>);
    expect(screen.getByText('Notice')).toHaveClass('py-lg');
  });
});

const options = [
  { value: 'a', label: 'Alpha' },
  { value: 'b', label: 'Beta' },
] as const;
test('tab strip shared gap follows settings and retains navigation callbacks', () => {
  const onChange = vi.fn();
  const node = (
    <TabList
      options={options}
      value="a"
      onChange={onChange}
      idPrefix="tabs"
      panelId="panel"
      ariaLabel="Tabs"
    />
  );
  const view = render(node);
  change(settings.tabList, 'gap', 'sm', () => {
    view.rerender(
      <TabList
        options={options}
        value="a"
        onChange={onChange}
        idPrefix="tabs"
        panelId="panel"
        ariaLabel="Tabs"
      />,
    );
    expect(screen.getByRole('tablist')).toHaveClass('gap-sm');
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Alpha' }), { key: 'ArrowRight' });
    expect(onChange).toHaveBeenCalledWith('b');
  });
});

test('segmented option padding propagates to every radio without altering keyboard selection', () => {
  const onChange = vi.fn();
  const view = render(<SegmentedControl options={options} value="a" onChange={onChange} />);
  change(settings.segmented.optionPadding.tray, 'y', 'lg', () => {
    view.rerender(<SegmentedControl options={options} value="a" onChange={onChange} />);
    for (const radio of screen.getAllByRole('radio')) expect(radio).toHaveClass('py-lg');
    fireEvent.keyDown(screen.getByRole('radiogroup'), { key: 'End' });
    expect(onChange).toHaveBeenCalledWith('b');
  });
});

test('select trigger follows the shared control height and option padding remains centrally managed', () => {
  const onChange = vi.fn();
  const view = render(
    <Select options={[...options]} value="a" onChange={onChange} ariaLabel="Choice" />,
  );
  change(settings.controls.height, 'md', 'touch', () => {
    view.rerender(
      <Select options={[...options]} value="a" onChange={onChange} ariaLabel="Choice" />,
    );
    expect(screen.getByRole('combobox')).toHaveClass('h-touch');
  });
  change(settings.select.optionPadding, 'y', 'md', () => {
    view.rerender(
      <Select options={[...options]} value="a" onChange={onChange} ariaLabel="Choice" />,
    );
    fireEvent.click(screen.getByRole('combobox'));
    for (const option of screen.getAllByRole('option')) expect(option).toHaveClass('py-md');
    fireEvent.click(screen.getByRole('option', { name: 'Beta' }));
    expect(onChange).toHaveBeenCalledWith('b');
  });
});

test('selectable card shell and its action target share a configured radius', () => {
  const onSelect = vi.fn();
  const props = {
    surfaceId: 'surface',
    name: 'Card',
    meta: 'Meta',
    status: null,
    selected: false,
    actionsOpen: false,
    actionsLabel: 'Actions',
    actions: null,
    onSelect,
    onToggleActions: vi.fn(),
  };
  const view = render(<SelectableCard {...props} />);
  change(settings.selectableCard, 'radius', 'md', () => {
    view.rerender(<SelectableCard {...props} />);
    const button = screen.getByRole('button', { name: 'Card' });
    expect(button).toHaveClass('rounded-md');
    expect(button.parentElement).toHaveClass('rounded-md', 'p-lg');
    fireEvent.click(button);
    expect(onSelect).toHaveBeenCalledOnce();
  });
});

test('switch thumb size is managed while checked and disabled behavior stays native', () => {
  const onChange = vi.fn();
  const view = render(<Switch checked={false} onChange={onChange} label="Enable" />);
  change(settings.switch, 'thumbSize', 'glyph', () => {
    view.rerender(<Switch checked onChange={onChange} label="Enable" />);
    expect(screen.getByRole('switch').firstElementChild).toHaveClass(
      'size-glyph',
      'translate-x-[21px]',
    );
    fireEvent.click(screen.getByRole('switch'));
    expect(onChange).toHaveBeenCalledWith(false);
    view.rerender(<Switch checked disabled onChange={onChange} label="Enable" />);
    expect(screen.getByRole('switch')).toBeDisabled();
  });
});

test('help badge and bubble follow central geometry while preserving accessible explanation', () => {
  const view = render(<HelpHint text="Explanation" example="Example" />);
  change(settings.helpHint, 'bubblePadding', 'lg', () => {
    view.rerender(<HelpHint text="Explanation" example="Example" />);
    expect(screen.getByRole('tooltip', { hidden: true })).toHaveClass('p-lg');
    expect(screen.getByRole('note')).toHaveAccessibleName('Explanation\nExample');
  });
});

test('spinner size follows central settings without losing its accessible decorative contract', () => {
  const view = render(<Spinner size="md" />);
  change(settings.spinner.size, 'md', 'glyph', () => {
    view.rerender(<Spinner size="md" />);
    expect(view.container.firstElementChild).toHaveClass('size-glyph', 'border-2');
    expect(view.container.firstElementChild).toHaveAttribute('aria-hidden', 'true');
  });
});

test('modal shell insets and width propagate without changing backdrop or Escape closing', () => {
  const onClose = vi.fn();
  const view = render(
    <Modal label="Dialog" onClose={onClose}>
      Body
    </Modal>,
  );
  change(settings.modal.shell, 'mobilePadding', 'md', () => {
    view.rerender(
      <Modal label="Dialog" onClose={onClose}>
        Body
      </Modal>,
    );
    expect(screen.getByRole('dialog').parentElement).toHaveClass('p-md', 'sm:p-xl');
    expect(screen.getByRole('dialog').parentElement).not.toHaveClass('p-lg');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();
  });
});

test('field and composer presets follow configured shape and typography', () => {
  const view = render(
    <>
      <Input variant="inline" aria-label="Field" />
      <Textarea variant="composer" aria-label="Composer" />
    </>,
  );
  change(settings.controls.variantRadii, 'inline', 'md', () => {
    view.rerender(
      <>
        <Input variant="inline" aria-label="Field" />
        <Textarea variant="composer" aria-label="Composer" />
      </>,
    );
    expect(screen.getByRole('textbox', { name: 'Field' })).toHaveClass('rounded-md');
  });
  change(settings.controls.areaRadii, 'composer', 'md', () => {
    view.rerender(
      <>
        <Input variant="inline" aria-label="Field" />
        <Textarea variant="composer" aria-label="Composer" />
      </>,
    );
    expect(screen.getByRole('textbox', { name: 'Composer' })).toHaveClass(
      'rounded-md',
      'min-h-control',
      'type-prose',
    );
  });
});

test('authentication field and chat composer preserve their original ink and weight', () => {
  render(
    <label className="type-caption">
      <Input variant="auth" aria-label="Auth" />
      <Textarea variant="composer" aria-label="Message" />
    </label>,
  );
  expect(screen.getByRole('textbox', { name: 'Auth' })).toHaveClass(
    'font-normal',
    'text-content-primary',
  );
  expect(screen.getByRole('textbox', { name: 'Message' })).toHaveClass('text-content-primary');
});

test('runtime stat tiles retain card fill above the grid separator tint', () => {
  render(
    <div className="bg-info-hairline">
      <StatTile variant="runtime" label="Runtime" value={1} />
    </div>,
  );
  expect(screen.getByText('Runtime').parentElement).toHaveClass('bg-surface-card');
});

test('a column dialog header stretches its rows while the default header stays centered', () => {
  render(
    <>
      <ModalHeader flow="column" data-testid="column">
        Column
      </ModalHeader>
      <ModalHeader data-testid="row">Row</ModalHeader>
    </>,
  );
  expect(screen.getByTestId('column')).toHaveClass('flex-col', 'items-stretch');
  expect(screen.getByTestId('column')).not.toHaveClass('items-center');
  expect(screen.getByTestId('row')).toHaveClass('items-center');
});

test('generic mobile table title cells preserve consumer typography rather than inventing emphasis', () => {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(375);
  try {
    render(
      <DataTable
        data={[{ name: 'Plain timestamp' }]}
        columns={[
          {
            id: 'name',
            header: 'Name',
            cell: ({ row }) => row.original.name,
            meta: { cardSlot: 'title' },
          },
        ]}
      />,
    );
    const title = screen.getByText('Plain timestamp');
    expect(title).toHaveClass('min-w-0', 'flex-1');
    expect(title).not.toHaveClass('type-compact-title');
  } finally {
    vi.restoreAllMocks();
  }
});

test('launch board preserves the original roomy gap at both layout widths', () => {
  expect(layoutSettings.launchBoard.gap).toBe('lg');
});
