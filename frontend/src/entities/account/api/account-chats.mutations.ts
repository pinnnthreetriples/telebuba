import {
  markAccountChatRead,
  sendAccountChatMessage,
  type ChatMedia,
  type ChatReadResult,
  type ChatSendResult,
} from '@/shared/api';
import type { AccountChatPeerType } from './account-chats.queries';

type ChatAddress = {
  accountId: string;
  peerType: AccountChatPeerType;
  peerId: string;
};

export async function markAccountChatReadRequest({
  accountId,
  peerType,
  peerId,
  maxMessageId,
}: ChatAddress & { maxMessageId: number }): Promise<ChatReadResult> {
  const { data } = await markAccountChatRead({
    path: { account_id: accountId, peer_type: peerType, peer_id: peerId },
    body: { max_message_id: maxMessageId },
    throwOnError: true,
  });
  return data;
}

export async function sendAccountChatMessageRequest({
  accountId,
  peerType,
  peerId,
  text,
  files,
}: ChatAddress & { text: string; files: File[] }): Promise<ChatSendResult> {
  const { data } = await sendAccountChatMessage({
    path: { account_id: accountId, peer_type: peerType, peer_id: peerId },
    body: {
      ...(text ? { text } : {}),
      ...(files.length > 0 ? { files } : {}),
    },
    throwOnError: true,
  });
  return data;
}

export function accountChatMediaFileName(media: ChatMedia, index: number): string {
  return media.file_name || `attachment-${String(index + 1)}`;
}
