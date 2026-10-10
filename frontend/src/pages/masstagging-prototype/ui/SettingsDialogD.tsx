// PROTOTYPE — throwaway. Variant D's settings dialog, shaped like the chat broadcast's:
// a header with the name and the "not saved" pill, four numbered sections, a footer
// with Save. Every explanation sits behind a "?" with one short example; the rarely
// touched flood limits are folded under «Защита от блокировок».
import { useQuery } from '@tanstack/react-query';
import { type ReactNode, useState } from 'react';

import {
  AccountAvatar,
  accountDisplayName,
  allAccountsQueryOptions,
  BulkAccountPicker,
} from '@/entities/account';
import {
  Badge,
  Button,
  HelpHint,
  Icon,
  IconButton,
  Input,
  Modal,
  Notice,
  SegmentedControl,
  Select,
  Switch,
  Textarea,
} from '@/shared/ui';

import { ACCOUNTS, type Draft, estimate, FREE_CAPTION_LIMIT, type Mode, parseTargets, type Patch } from './model';
import { Num, NumRange, Row } from './parts';

const MODES: { value: Mode; title: string; line: string }[] = [
  { value: 'stories', title: 'Отметки в сторис', line: 'Сторис с @отметками' },
  { value: 'priming', title: 'Прайминг', line: 'Без сторис, только личка' },
];

function Section({
  n,
  title,
  hint,
  example,
  aside,
  children,
}: {
  n?: number;
  title: string;
  hint: string;
  example?: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        {n === undefined ? null : (
          <span className="flex size-chip items-center justify-center rounded-full bg-info-tint text-small font-medium text-info-strong">
            {n}
          </span>
        )}
        <span className="type-h3">{title}</span>
        <HelpHint text={hint} example={example} />
        {aside}
      </div>
      {children}
    </section>
  );
}

function ModeSection({ draft, patch }: { draft: Draft; patch: Patch }) {
  return (
    <Section
      n={1}
      title="Режим"
      hint="Сторис — аккаунт выкладывает сторис и отмечает в ней людей. Прайминг — ничего не публикует, только мелькает у человека в личке."
      example="Человек видит «Анна отметила вас в истории» или «Анна включила автоудаление» — и идёт смотреть профиль."
    >
      <SegmentedControl
        variant="outline"
        className="grid gap-3 sm:grid-cols-2"
        value={draft.mode}
        ariaLabel="Режим"
        options={MODES.map((mode) => ({
          value: mode.value,
          label: (
            <span className="block py-1 text-left">
              <span className="block type-body-medium">{mode.title}</span>
              <span className="block type-small">{mode.line}</span>
            </span>
          ),
        }))}
        onChange={(mode) => {
          patch({ mode });
        }}
      />
    </Section>
  );
}

// The chat broadcast's strip: "+" opens the app's fleet picker, the chosen accounts sit
// as avatars, the cross on hover takes one out. A spam-blocked one stays but is faded.
function AccountsSection({ draft, patch }: { draft: Draft; patch: Patch }) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const fleet = useQuery(allAccountsQueryOptions());
  const byId = new Map((fleet.data?.items ?? []).map((account) => [account.account_id, account]));
  const { free, excluded } = estimate(draft);
  const blocked = new Set(ACCOUNTS.filter((account) => account.spamblock).map((account) => account.id));
  return (
    <Section
      n={2}
      title="Аккаунты"
      hint="Кто будет отмечать. Аккаунты со спамблоком не участвуют — их отметки не доходят."
      example={draft.mode === 'stories' ? 'Без Premium — 1 сторис в день. С Premium — до 100.' : undefined}
      aside={
        <>
          {draft.mode === 'stories' && free > 0 ? <Badge tone="warning">без Premium: {free}</Badge> : null}
          {excluded > 0 ? <Badge tone="danger">спамблок: {excluded}</Badge> : null}
        </>
      }
    >
      <div className="flex items-center gap-3">
        <IconButton
          size="sm"
          aria-label="Добавить аккаунты"
          onClick={() => {
            setPickerOpen(true);
          }}
        >
          <Icon name="plus" size={16} />
        </IconButton>
        {draft.accountIds.length === 0 ? (
          <span className="type-body text-content-subtle">Пока никого — нажмите «+»</span>
        ) : (
          <div className="tb-scroll flex flex-1 items-center gap-2 overflow-x-auto py-1">
            {draft.accountIds.map((id) => {
              const account = byId.get(id);
              const name = account === undefined ? id : accountDisplayName(account);
              return (
                <span
                  key={id}
                  className={`group relative shrink-0 ${blocked.has(id) ? 'opacity-50' : ''}`}
                  title={blocked.has(id) ? `${name} — спамблок, не участвует` : name}
                >
                  {account === undefined ? (
                    <span className="flex size-tile items-center justify-center rounded-full bg-canvas text-content-muted type-body-medium">
                      ?
                    </span>
                  ) : (
                    <AccountAvatar
                      account={account}
                      className="size-tile rounded-full"
                      fallbackClassName="bg-canvas text-content-muted type-body-medium"
                    />
                  )}
                  <IconButton
                    size="sm"
                    shape="circle"
                    aria-label={`Убрать ${name}`}
                    onClick={() => {
                      patch({ accountIds: draft.accountIds.filter((other) => other !== id) });
                    }}
                    className="absolute -right-1 -top-1 bg-surface-card opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
                  >
                    <Icon name="close" size={16} />
                  </IconButton>
                </span>
              );
            })}
          </div>
        )}
      </div>
      {pickerOpen ? (
        <BulkAccountPicker
          selected={draft.accountIds}
          onApply={(accountIds) => {
            patch({ accountIds });
            setPickerOpen(false);
          }}
          onClose={() => {
            setPickerOpen(false);
          }}
        />
      ) : null}
    </Section>
  );
}

const CHIPS_SHOWN = 8;

function TargetsSection({ draft, patch }: { draft: Draft; patch: Patch }) {
  const [entry, setEntry] = useState('');
  const [all, setAll] = useState(false);
  const parsed = parseTargets(entry).filter((target) => !draft.targets.includes(target));
  const add = () => {
    if (parsed.length > 0) patch({ targets: [...draft.targets, ...parsed] });
    setEntry('');
  };
  const shown = all ? draft.targets : draft.targets.slice(0, CHIPS_SHOWN);
  return (
    <Section
      n={3}
      title="Кого отмечать"
      hint="По одному в строке: username, телефон или ссылка на чат — из чата возьмём его участников."
      example="@ivan · +79001234567 · t.me/moscow_chat"
      aside={draft.targets.length > 0 ? <Badge>{draft.targets.length}</Badge> : undefined}
    >
      <SegmentedControl
        variant="tray"
        value={draft.targetSource}
        ariaLabel="Откуда цели"
        options={[
          { value: 'manual', label: 'Свой список' },
          { value: 'contacts', label: 'Контакты аккаунтов' },
        ]}
        onChange={(targetSource) => {
          patch({ targetSource });
        }}
      />
      {draft.targetSource === 'contacts' ? (
        <span className="type-body text-content-subtle">Каждый аккаунт отметит свои контакты.</span>
      ) : (
        <>
          <Textarea
            value={entry}
            placeholder={'@username\n+79001234567\nt.me/chat'}
            onChange={(event) => {
              setEntry(event.target.value);
            }}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="primary" disabled={parsed.length === 0} onClick={add}>
              <Icon name="plus" size={14} />
              {parsed.length > 0 ? `Добавить ${String(parsed.length)}` : 'Добавить'}
            </Button>
            <Button size="sm">
              <Icon name="upload-cloud" size={14} />
              Из файла
            </Button>
            {draft.targets.length > 0 ? (
              <Button
                size="sm"
                variant="ghost"
                className="ml-auto"
                onClick={() => {
                  patch({ targets: [] });
                }}
              >
                Очистить
              </Button>
            ) : null}
          </div>
          {draft.targets.length === 0 ? null : (
            <div className="flex flex-wrap items-center gap-2">
              {shown.map((target) => (
                <span
                  key={target}
                  className="inline-flex items-center gap-1 rounded-full border border-line bg-canvas py-1 pl-3 pr-1 text-body"
                >
                  {target}
                  <IconButton
                    size="sm"
                    shape="circle"
                    aria-label={`Убрать ${target}`}
                    onClick={() => {
                      patch({ targets: draft.targets.filter((t) => t !== target) });
                    }}
                  >
                    <Icon name="close" size={14} />
                  </IconButton>
                </span>
              ))}
              {draft.targets.length > CHIPS_SHOWN ? (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setAll((on) => !on);
                  }}
                >
                  {all ? 'Свернуть' : `ещё ${String(draft.targets.length - CHIPS_SHOWN)}`}
                </Button>
              ) : null}
            </div>
          )}
        </>
      )}
    </Section>
  );
}

function StorySection({ draft, patch }: { draft: Draft; patch: Patch }) {
  return (
    <Section
      n={4}
      title="Сторис"
      hint="Что увидит человек, когда откроет отметку."
      example="Видео 15 с + подпись «Бесплатный урок» + кнопка-ссылка."
    >
      <div className="flex flex-col items-center gap-1 rounded-md border border-dashed border-line-strong bg-canvas px-4 py-6 text-center">
        <Icon name="upload-cloud" size={20} />
        <span className="flex items-center gap-1 text-body">
          Перетащите фото или видео
          <HelpHint text="Если файлов несколько — сторис берут их по очереди." example="Видео — до 60 секунд." />
        </span>
      </div>
      {draft.media.length === 0 ? null : (
        <div className="flex flex-wrap gap-2">
          {draft.media.map((file) => (
            <Badge key={file} size="sm">
              <Icon name="video" size={12} /> {file}
            </Badge>
          ))}
        </div>
      )}
      <label className="flex flex-col gap-1">
        <span className="flex items-center gap-1">
          <span className="type-body-medium">Подпись</span>
          <HelpHint text="Отметки людей допишем сами." example="«Смотри урок» → «Смотри урок @ivan @olga»" />
          <span className="ml-auto type-small tabular-nums">
            {draft.caption.length}/{FREE_CAPTION_LIMIT}
          </span>
        </span>
        <Input
          size="sm"
          value={draft.caption}
          onChange={(event) => {
            patch({ caption: event.target.value });
          }}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="flex items-center gap-1">
          <span className="type-body-medium">Ссылка</span>
          <HelpHint text="Кнопка на сторис. Работает только у аккаунтов с Premium." example="https://t.me/your_channel" />
          <Badge tone="info" className="ml-1">
            Premium
          </Badge>
        </span>
        <Input
          size="sm"
          value={draft.link}
          placeholder="https://"
          onChange={(event) => {
            patch({ link: event.target.value });
          }}
        />
      </label>
      {draft.link === '' ? null : (
        <label className="flex flex-col gap-1">
          <span className="type-body-medium">Текст на кнопке</span>
          <Input
            size="sm"
            value={draft.plateText}
            placeholder="Смотреть"
            onChange={(event) => {
              patch({ plateText: event.target.value });
            }}
          />
        </label>
      )}
      <div>
        <Row
          first
          label="Кто видит"
          hint="Ставьте «Все» — иначе отмеченный может не увидеть сторис."
        >
          <SegmentedControl
            variant="pill"
            value={draft.visibility}
            ariaLabel="Кто видит сторис"
            options={[
              { value: 'all', label: 'Все' },
              { value: 'contacts', label: 'Контакты' },
            ]}
            onChange={(visibility) => {
              patch({ visibility });
            }}
          />
        </Row>
        <Row label="Сколько висит" hint="Дольше висит — больше переходов." example="Без Premium — только 24 часа.">
          <Select
            value={draft.lifetime}
            ariaLabel="Сколько висит сторис"
            options={[
              { value: '6', label: '6 ч' },
              { value: '12', label: '12 ч' },
              { value: '24', label: '24 ч' },
              { value: '48', label: '48 ч' },
            ]}
            onChange={(value) => {
              patch({ lifetime: value as Draft['lifetime'] });
            }}
          />
        </Row>
        <Row
          label="Сначала создать диалог"
          hint="Перед отметкой аккаунт на секунду включает автоудаление в личке — так уведомление точно дойдёт."
        >
          <Switch
            checked={draft.primeFirst}
            label="Сначала создать диалог"
            onChange={(primeFirst) => {
              patch({ primeFirst });
            }}
          />
        </Row>
      </div>
    </Section>
  );
}

function PaceSection({ n, draft, patch }: { n?: number; draft: Draft; patch: Patch }) {
  const [safetyOpen, setSafetyOpen] = useState(false);
  const story = draft.mode === 'stories';
  return (
    <Section
      n={n}
      title="Темп"
      hint="Как быстро работают аккаунты. Медленнее — безопаснее."
      example="Значения по умолчанию подходят для начала."
    >
      <div>
        {story ? (
          <>
            <Row first label="Отметок в одной сторис" hint="Сколько людей в одной сторис." example="5 — безопасно.">
              <Num
                value={draft.mentionsPerStory}
                label="Отметок в одной сторис"
                onChange={(mentionsPerStory) => {
                  patch({ mentionsPerStory });
                }}
              />
            </Row>
            <Row label="Сторис в сутки" hint="С одного аккаунта." example="Без Premium — 1.">
              <Num
                value={draft.storiesPerDay}
                label="Сторис в сутки"
                onChange={(storiesPerDay) => {
                  patch({ storiesPerDay });
                }}
              />
            </Row>
            <Row label="Пауза между сторис" hint="Каждый раз случайная — в этих пределах." example="1–2 мин">
              <NumRange
                min={draft.storyGapMin}
                max={draft.storyGapMax}
                label="Пауза между сторис"
                unit="мин"
                onChange={(storyGapMin, storyGapMax) => {
                  patch({ storyGapMin, storyGapMax });
                }}
              />
            </Row>
          </>
        ) : (
          <>
            <Row first label="Людей в сутки" hint="С одного аккаунта." example="Новый — 5–6, прогретый — до 40.">
              <Num
                value={draft.dialogsPerDay}
                label="Людей в сутки"
                onChange={(dialogsPerDay) => {
                  patch({ dialogsPerDay });
                }}
              />
            </Row>
            <Row label="Пауза между людьми" hint="Каждый раз случайная — в этих пределах." example="20–60 с">
              <NumRange
                min={draft.dialogDelayMin}
                max={draft.dialogDelayMax}
                label="Пауза между людьми"
                unit="с"
                onChange={(dialogDelayMin, dialogDelayMax) => {
                  patch({ dialogDelayMin, dialogDelayMax });
                }}
              />
            </Row>
          </>
        )}
        <Row label="Мягкий старт" hint="Первые 3 дня — меньше людей и длиннее паузы." example="5–6 в день с паузой 3–10 мин, потом обычный темп.">
          <Switch
            checked={draft.rampUp}
            label="Мягкий старт"
            onChange={(rampUp) => {
              patch({ rampUp });
            }}
          />
        </Row>
        <Row label="Пропускать уже отмеченных" hint="Не трогать тех, кого уже отметили в любой кампании.">
          <Switch
            checked={draft.skipTagged}
            label="Пропускать уже отмеченных"
            onChange={(skipTagged) => {
              patch({ skipTagged });
            }}
          />
        </Row>
        <Row label="Остановиться через" hint="На каждый аккаунт за один запуск." example="0 — пока не кончится список.">
          <span className="flex items-center gap-2">
            <Num
              value={draft.volume === 'count' ? draft.maxTags : draft.maxMinutes}
              label="Остановиться через"
              onChange={(value) => {
                patch(draft.volume === 'count' ? { maxTags: value } : { maxMinutes: value });
              }}
            />
            <SegmentedControl
              variant="pill"
              value={draft.volume}
              ariaLabel="Единица"
              options={[
                { value: 'count', label: 'отметок' },
                { value: 'time', label: 'минут' },
              ]}
              onChange={(volume) => {
                patch({ volume });
              }}
            />
          </span>
        </Row>
      </div>
      <div className="rounded-md border border-line">
        <button
          type="button"
          aria-expanded={safetyOpen}
          onClick={() => {
            setSafetyOpen((on) => !on);
          }}
          className="flex w-full items-center gap-2 px-4 py-3 text-left"
        >
          <Icon name="shield-check" size={16} />
          <span className="type-body-medium">Защита от блокировок</span>
          <span className="type-small">
            пауза {draft.floodPause} с · стоп после {draft.floodsToQuarantine}
          </span>
          <span className={`ml-auto flex transition-transform ${safetyOpen ? 'rotate-180' : ''}`}>
            <Icon name="chevron-down" size={16} />
          </span>
        </button>
        {safetyOpen ? (
          <div className="border-t border-canvas px-4 pb-2">
            <Row first label="Пауза при флуд-лимите" hint="Telegram попросил подождать — ждём столько." example="500 с">
              <Num
                value={draft.floodPause}
                label="Пауза при флуд-лимите"
                unit="с"
                onChange={(floodPause) => {
                  patch({ floodPause });
                }}
              />
            </Row>
            <Row label="Стоп после флуд-лимитов" hint="Столько раз подряд — и аккаунт отдыхает до завтра." example="3">
              <Num
                value={draft.floodsToQuarantine}
                label="Стоп после флуд-лимитов"
                onChange={(floodsToQuarantine) => {
                  patch({ floodsToQuarantine });
                }}
              />
            </Row>
          </div>
        ) : null}
      </div>
    </Section>
  );
}

export function SettingsDialogD({
  name,
  initial,
  locked,
  onClose,
  onSave,
}: {
  name: string;
  initial: Draft;
  locked: boolean;
  onClose: () => void;
  onSave: (draft: Draft) => void;
}) {
  const [draft, setDraft] = useState<Draft>(initial);
  const patch: Patch = (next) => {
    setDraft((prev) => ({ ...prev, ...next }));
  };
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial);
  const story = draft.mode === 'stories';
  return (
    <Modal onClose={onClose} dirty={dirty} size="form" label="Настройки кампании">
      {(close) => (
        <>
          <div className="flex items-center gap-3 border-b border-canvas px-6 pb-4 pt-6">
            <div className="min-w-0">
              <div className="truncate type-h2">{name}</div>
              <div className="mt-1 type-small">Настройки кампании</div>
            </div>
            <div className="flex-1" />
            {dirty ? (
              <span className="shrink-0 rounded-full bg-warning-tint px-3 py-1 text-small font-medium text-warning-deep">
                Не сохранено
              </span>
            ) : null}
          </div>
          {locked ? (
            <div className="px-6 pt-4" role="alert">
              <Notice tone="info">Кампания идёт. Поставьте на паузу, чтобы менять настройки.</Notice>
            </div>
          ) : null}
          <fieldset disabled={locked} className="m-0 min-w-0 border-0 p-0">
            <div className="flex min-w-0 flex-col gap-8 px-6 py-6">
              <ModeSection draft={draft} patch={patch} />
              <AccountsSection draft={draft} patch={patch} />
              <TargetsSection draft={draft} patch={patch} />
              {story ? <StorySection draft={draft} patch={patch} /> : null}
              <PaceSection n={story ? 5 : 4} draft={draft} patch={patch} />
            </div>
          </fieldset>
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-canvas px-6 py-4">
            <Button size="sm" onClick={close}>
              Отмена
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={locked || !dirty}
              onClick={() => {
                onSave(draft);
                onClose();
              }}
            >
              Сохранить
            </Button>
          </div>
        </>
      )}
    </Modal>
  );
}

// The board's gear: only «Темп», and it changes on the fly — no pause needed.
export function PaceDialog({
  initial,
  onClose,
  onSave,
}: {
  initial: Draft;
  onClose: () => void;
  onSave: (draft: Draft) => void;
}) {
  const [draft, setDraft] = useState<Draft>(initial);
  const patch: Patch = (next) => {
    setDraft((prev) => ({ ...prev, ...next }));
  };
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial);
  return (
    <Modal onClose={onClose} dirty={dirty} size="form" label="Темп">
      {(close) => (
        <>
          <div className="px-6 pt-6">
            <PaceSection draft={draft} patch={patch} />
          </div>
          <div className="mt-6 flex justify-end gap-2 border-t border-canvas px-6 py-4">
            <Button size="sm" onClick={close}>
              Отмена
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={!dirty}
              onClick={() => {
                onSave(draft);
                onClose();
              }}
            >
              Сохранить
            </Button>
          </div>
        </>
      )}
    </Modal>
  );
}
