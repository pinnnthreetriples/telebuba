import { fireEvent, screen } from '@testing-library/react';

// The caption is a contenteditable editor, not an <input>: text goes in as a DOM edit
// followed by the `input` event the browser would fire.
export function typeCaption(text: string): void {
  const editor = screen.getByRole('textbox', { name: 'Подпись' });
  editor.appendChild(document.createTextNode(text));
  fireEvent.input(editor);
}
