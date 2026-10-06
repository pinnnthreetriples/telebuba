// The settings dialog's ONE draft. Every section edits it and the "check how it will go"
// window reads it, so the preview shows exactly what Save would write — no section keeps
// numbers of its own. Server shape in, server shape out; the only additions are a local
// id per message (stable React keys while the chain is reordered or trimmed) and the
// object URL of a photo picked in this session.
import type {
  ChatBroadcastMessage,
  ChatBroadcastSettings,
  ChatBroadcastSettingsRead,
  ChatBroadcastSettingsUpdate,
} from '@/shared/api';

export type Settings = Required<Omit<ChatBroadcastSettings, 'messages'>>;

export type MessageDraft = {
  id: number;
  kind: 'text' | 'post';
  text: string;
  photo: { mediaId: string; name: string; url: string | null } | null;
  post: string;
};

export type Draft = {
  name: string;
  accountIds: string[];
  settings: Settings;
  messages: MessageDraft[];
};

// The backend's defaults, mirrored so a never-saved campaign opens with the numbers the
// operator agreed on rather than with empty fields.
export const DEFAULT_SETTINGS: Settings = {
  target_mode: 'list',
  targets: [],
  own_excluded: [],
  first_message: 'template',
  ai_brief: '',
  randomize: true,
  approval_wait_hours: 24,
  join_delay_minutes: 60,
  between_chats: { min: 30, max: 90 },
  between_messages: { min: 3, max: 8 },
  typing: true,
  stop_mode: 'count',
  stop_messages: 100,
  stop_hours: 6,
  loop: true,
  rest_minutes: { min: 60, max: 120 },
  rounds: 3,
  skip_already_written: false,
  account_limit: true,
  per_hour: 20,
  per_day: 120,
  skip_errors: true,
  skip_deleted: true,
  max_consecutive_errors: 10,
};

export const MAX_MESSAGES = 10;

function messageDraft(message: ChatBroadcastMessage, id: number): MessageDraft {
  return {
    id,
    kind: message.kind ?? 'text',
    text: message.text ?? '',
    photo:
      message.photo === null || message.photo === undefined
        ? null
        : { mediaId: message.photo.media_id, name: message.photo.name, url: null },
    post: message.post ?? '',
  };
}

export function draftOf(read: ChatBroadcastSettingsRead): Draft {
  const { messages, ...rest } = read.settings;
  return {
    name: read.name,
    accountIds: [...read.account_ids],
    settings: { ...DEFAULT_SETTINGS, ...rest },
    messages: (messages ?? []).map((message, index) => messageDraft(message, index + 1)),
  };
}

export function emptyMessage(messages: MessageDraft[]): MessageDraft {
  const id = Math.max(0, ...messages.map((message) => message.id)) + 1;
  return { id, kind: 'text', text: '', photo: null, post: '' };
}

export function bodyOf(draft: Draft, expectedUpdatedAt: string): ChatBroadcastSettingsUpdate {
  return {
    expected_updated_at: expectedUpdatedAt,
    name: draft.name.trim() || draft.name,
    account_ids: draft.accountIds,
    settings: {
      ...draft.settings,
      messages: draft.messages.map((message) => ({
        kind: message.kind,
        text: message.text,
        photo:
          message.photo === null
            ? null
            : { media_id: message.photo.mediaId, name: message.photo.name },
        post: message.post,
      })),
    },
  };
}

// What the server would store: photo previews are a session detail, not a change.
export function sameDraft(a: Draft, b: Draft): boolean {
  return JSON.stringify(bodyOf(a, '')) === JSON.stringify(bodyOf(b, ''));
}

// A message that will actually be sent — the rule the engine applies on the server.
export function isFilled(message: MessageDraft, draft: Draft, index: number): boolean {
  if (message.kind === 'post') return POST_LINK.test(message.post.trim());
  const aiWritesIt =
    index === 0 && draft.settings.first_message === 'ai' && draft.settings.ai_brief.trim() !== '';
  return message.text.trim() !== '' || message.photo !== null || aiWritesIt;
}

export const POST_LINK =
  /^(?:https?:\/\/)?(?:t\.me|telegram\.me)\/(?:c\/\d+|@?\w{3,32})\/(?:\d+\/)?\d+\/?$/i;

export const VARIANTS = /\{[^{}]*\|[^{}]*\}/;

// One pasted blob → one link per item, the way the server splits it.
export function splitTargets(value: string): string[] {
  return value
    .split(/[\s,;]+/)
    .map((item) => item.trim())
    .filter((item) => item !== '');
}
