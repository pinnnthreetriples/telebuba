import { useCallback, useEffect, useState } from 'react';

// ПРОТОТИП «Прогрев пачками». Папки пока живут только в браузере: бэкенда у них ещё
// нет, поэтому темп папки рисуется, но прогрев его не читает (тонкая настройка
// действий по папке — следующий этап). Длительность, тип, устройство и первый заход применяются по-настоящему —
// через обычный старт аккаунта, с настройками папки вместо настроек окна.

export type Persona = 'calm' | 'normal' | 'active';
export const PERSONAS: readonly Persona[] = ['calm', 'normal', 'active'];
export type PersonaMix = Record<Persona, number>;

// `primary`: каждый аккаунт идёт со своего основного устройства (как «Запустить пул»).
export type FolderDevice = 'desktop' | 'mobile' | 'primary';
export type FolderPace = 'inherit' | 'careful' | 'normal' | 'fast';
export type FolderFinish = 'keep' | 'handoff';

export interface WarmingFolder {
  id: string;
  name: string;
  days: number;
  mix: PersonaMix;
  device: FolderDevice;
  delayHours: number;
  // Рассыпать старты пачки по окну первого захода, а не будить всех в одну минуту.
  spread: boolean;
  pace: FolderPace;
  finish: FolderFinish;
}

// Тип данных, с которым готовый аккаунт тащится в папку.
export const ACCOUNT_DRAG_TYPE = 'application/x-telebuba-warming-account';

// Внутри какой папки оператор: id папки или «Без папки». null — экран всех папок.
export const UNFILED = 'unfiled';

interface FolderState {
  folders: WarmingFolder[];
  // account_id → id папки. У аккаунта ровно одна папка прогрева или ни одной.
  assignment: Record<string, string>;
}

const STORAGE_KEY = 'tb.warmingFolders.prototype';
const EMPTY: FolderState = { folders: [], assignment: {} };

function load(): FolderState {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return EMPTY;
    const parsed = JSON.parse(raw) as Partial<FolderState>;
    return { folders: parsed.folders ?? [], assignment: parsed.assignment ?? {} };
  } catch {
    return EMPTY;
  }
}

export function newFolder(defaultDays: number): WarmingFolder {
  return {
    id: '',
    name: '',
    days: defaultDays,
    mix: { calm: 30, normal: 50, active: 20 },
    device: 'desktop',
    delayHours: 3,
    spread: true,
    pace: 'inherit',
    finish: 'keep',
  };
}

// Тип пользователя i-го аккаунта пачки по смеси папки: детерминированно и без
// перекоса — первые аккаунты пачки не все «спокойные».
export function personaFor(mix: PersonaMix, index: number): Persona {
  const total = PERSONAS.reduce((sum, p) => sum + mix[p], 0) || 1;
  // Золотое сечение раскладывает подряд идущие индексы по отрезку равномерно.
  const point = ((index * 0.618_033_988_7) % 1) * total;
  let edge = 0;
  for (const persona of PERSONAS) {
    edge += mix[persona];
    if (point < edge) return persona;
  }
  return 'normal';
}

export function useWarmingFolders() {
  const [state, setState] = useState<FolderState>(load);

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      // Приватное окно или запрет хранилища: папки живут до перезагрузки.
    }
  }, [state]);

  const save = useCallback((folder: WarmingFolder): string => {
    const id = folder.id || `wf-${Date.now().toString(36)}`;
    setState((s) => ({
      ...s,
      folders: s.folders.some((f) => f.id === id)
        ? s.folders.map((f) => (f.id === id ? { ...folder, id } : f))
        : [...s.folders, { ...folder, id }],
    }));
    return id;
  }, []);

  const remove = useCallback((folderId: string) => {
    setState((s) => ({
      folders: s.folders.filter((f) => f.id !== folderId),
      assignment: Object.fromEntries(
        Object.entries(s.assignment).filter(([, id]) => id !== folderId),
      ),
    }));
  }, []);

  const assign = useCallback((accountIds: string[], folderId: string | null) => {
    setState((s) => {
      const next = { ...s.assignment };
      for (const id of accountIds) {
        if (folderId) next[id] = folderId;
        else delete next[id];
      }
      return { ...s, assignment: next };
    });
  }, []);

  const folderOf = useCallback(
    (accountId: string): WarmingFolder | undefined => {
      const id = state.assignment[accountId];
      return id ? state.folders.find((f) => f.id === id) : undefined;
    },
    [state],
  );

  return { folders: state.folders, assignment: state.assignment, save, remove, assign, folderOf };
}
