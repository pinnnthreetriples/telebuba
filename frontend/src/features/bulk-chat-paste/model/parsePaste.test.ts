import { describe, expect, test } from 'vitest';

import { analyzePaste, chatLinkKey } from './parsePaste';

const FOLDERS = { allowFolders: true };
const NO_FOLDERS = { allowFolders: false };

describe('chatLinkKey', () => {
  test.each([
    ['@Chat_Name', 'chat_name'],
    ['chat_name', 'chat_name'],
    ['t.me/Chat_Name', 'chat_name'],
    ['https://t.me/chat_name/', 'chat_name'],
    ['HTTPS://T.ME/chat_name?single', 'chat_name'],
    ['telegram.me/chat_name', 'chat_name'],
    ['https://t.me/chat_name/123', 'chat_name'],
    ['addlistfan', 'addlistfan'],
  ])('%s is the public chat %s', (raw, key) => {
    expect(chatLinkKey(raw, NO_FOLDERS)).toEqual({ key, kind: 'public' });
  });

  test('an invite keeps the case of its hash, in both spellings', () => {
    expect(chatLinkKey('https://t.me/+AbCdEfGh12', NO_FOLDERS)).toEqual({
      key: '+AbCdEfGh12',
      kind: 'invite',
    });
    expect(chatLinkKey('t.me/joinchat/AbCdEfGh12', NO_FOLDERS)?.key).toBe('+AbCdEfGh12');
  });

  test('a folder is a target only where folders are', () => {
    expect(chatLinkKey('t.me/addlist/Slug-1', FOLDERS)).toEqual({
      key: 'addlist:Slug-1',
      kind: 'folder',
    });
    expect(chatLinkKey('t.me/addlist/Slug-1', NO_FOLDERS)).toBeNull();
    expect(chatLinkKey('t.me/addlist/', FOLDERS)).toBeNull();
  });

  test('where folders open, `addlist…` is a folder or nothing, as the server reads it', () => {
    // `parse_target` takes `addlist/slug` without the `t.me/` prefix and refuses any other
    // value that starts with `addlist`, so a public `addlistfan` would be dropped there.
    expect(chatLinkKey('addlist/Slug-1', FOLDERS)?.key).toBe('addlist:Slug-1');
    expect(chatLinkKey('addlistfan', FOLDERS)).toBeNull();
    expect(chatLinkKey('t.me/addlistfan', FOLDERS)).toBeNull();
    expect(chatLinkKey('@addlistfan', FOLDERS)?.key).toBe('addlistfan');
    expect(chatLinkKey('t.me/addlist', NO_FOLDERS)).toBeNull();
  });

  test('a run of digits is a username the server takes', () => {
    expect(chatLinkKey('12345', NO_FOLDERS)).toEqual({ key: '12345', kind: 'public' });
  });

  test.each([
    '!!bad',
    'ab',
    'чат',
    'channel/123',
    't.me/c/12345/1',
    't.me/+short',
    'https://example.com/chat',
    '@',
    'a'.repeat(33),
  ])('%s is not a chat link', (raw) => {
    expect(chatLinkKey(raw, FOLDERS)).toBeNull();
  });
});

describe('analyzePaste', () => {
  test('a column, a row and a comma list all split into links', () => {
    const result = analyzePaste('@one\r\n@two\t@three, @four;@five', [], NO_FOLDERS);
    expect(result.fresh).toEqual(['@one', '@two', '@three', '@four', '@five']);
  });

  test('list markers and wrapping punctuation are not links and not junk', () => {
    const result = analyzePaste('1. @one\n2) «@two»\n- (t.me/three).\n• @four', [], NO_FOLDERS);
    expect(result.fresh).toEqual(['@one', '@two', 't.me/three', '@four']);
    expect(result.invalid).toEqual([]);
  });

  test('a numbered list loses its numbers, but a number on its own is still a chat', () => {
    const result = analyzePaste('1. @one\n(2) @two\n100. @three\n12345', [], NO_FOLDERS);
    expect(result.fresh).toEqual(['@one', '@two', '@three', '12345']);
    expect(result.invalid).toEqual([]);
  });

  test('the list is read by the same rules as the paste', () => {
    // Neuroshilling keeps `addlistfan` as a chat, so pasting it again is a repeat.
    expect(analyzePaste('@addlistfan', ['addlistfan'], NO_FOLDERS).existing).toEqual([
      '@addlistfan',
    ]);
    expect(analyzePaste('addlist/abc', ['t.me/addlist/abc'], FOLDERS).existing).toEqual([
      'addlist/abc',
    ]);
  });

  test('a chat already in the list or repeated in the paste is reported, not added', () => {
    const result = analyzePaste(
      '@Alpha t.me/beta https://t.me/beta/ @gamma @GAMMA t.me/+AbCdEfGh12 t.me/+abcdefgh12',
      ['t.me/alpha', '@zeta'],
      NO_FOLDERS,
    );
    expect(result.fresh).toEqual(['t.me/beta', '@gamma', 't.me/+AbCdEfGh12', 't.me/+abcdefgh12']);
    expect(result.existing).toEqual(['@Alpha']);
    expect(result.repeated).toEqual(['https://t.me/beta/', '@GAMMA']);
  });

  test('junk is set aside and the rest still goes in', () => {
    const result = analyzePaste('@good !!bad t.me/addlist/abc', [], NO_FOLDERS);
    expect(result.fresh).toEqual(['@good']);
    expect(result.invalid).toEqual(['!!bad', 't.me/addlist/abc']);
  });

  test('an empty paste is empty', () => {
    expect(analyzePaste('  \n ', ['@a'], FOLDERS)).toEqual({
      fresh: [],
      existing: [],
      repeated: [],
      invalid: [],
    });
  });
});
