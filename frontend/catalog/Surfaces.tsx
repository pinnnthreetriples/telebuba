// Поверхности: Card, CollapsibleCard, Modal, ConfirmModal, DataTable, SurfHover.
//
// Диалоги показаны открытыми, а не кнопкой «открыть»: каталог снимается, и снимок
// закрытого диалога — это снимок кнопки. Оба Modal рендерятся по флагу, который тест
// умеет переключать через хеш адреса.
import { useState } from 'react';

import type { ColumnDef } from '@tanstack/react-table';

import {
  Badge,
  Button,
  Card,
  CollapsibleCard,
  ConfirmModal,
  DataTable,
  Icon,
  IconButton,
  Modal,
  SurfHover,
} from '@/shared/ui';

import { Cell, Row, Section } from './Frame';

type Account = { name: string; phone: string; state: string };

const ROWS: Account[] = [
  { name: 'Иван Петров', phone: '+7 900 111-22-33', state: 'Прогрет' },
  { name: 'Мария Смирнова', phone: '+7 900 444-55-66', state: 'В прогреве' },
  { name: 'Пётр Кузнецов', phone: '+7 900 777-88-99', state: 'Заблокирован' },
];

const COLUMNS: ColumnDef<Account>[] = [
  { id: 'name', header: 'Аккаунт', accessorKey: 'name' },
  { id: 'phone', header: 'Телефон', accessorKey: 'phone' },
  {
    id: 'state',
    header: 'Состояние',
    cell: ({ row }) => <Badge dot>{row.original.state}</Badge>,
  },
];

export function Surfaces() {
  const [modal, setModal] = useState(false);
  const [confirm, setConfirm] = useState(false);

  return (
    <Section
      id="surfaces"
      title="Поверхности"
      note="Card, CollapsibleCard, Modal и выпадающие панели получают фон, рамку, радиус и тень из общего набора вариантов поверхности. Card — карточка на странице, диалог — карточка над завесой, панель — вложенная в карточку."
    >
      <Row label="Card">
        <Cell caption="только тело">
          <div className="w-panel max-w-full">
            <Card>
              <p className="type-body text-content-subtle">
                Карточка без шапки: белая, волосяная рамка, rounded-lg.
              </p>
            </Card>
          </div>
        </Cell>
        <Cell caption="с заголовком">
          <div className="w-panel max-w-full">
            <Card title="Прокси" subtitle="12 из 40 занято">
              <p className="type-body text-content-subtle">
                Заголовок и подзаголовок — роли карточки, не размеры.
              </p>
            </Card>
          </div>
        </Cell>
      </Row>

      <Row label="CollapsibleCard">
        <Cell caption="закрыта">
          <div className="w-panel max-w-full">
            <CollapsibleCard
              label="Ограничения"
              header={<span className="type-h3">Ограничения</span>}
            >
              <p className="type-body text-content-subtle">Тело раскрывается по клику на шапку.</p>
            </CollapsibleCard>
          </div>
        </Cell>
        <Cell caption="открыта">
          <div className="w-panel max-w-full">
            <CollapsibleCard
              defaultOpen
              label="Ограничения"
              header={<span className="type-h3">Ограничения</span>}
              trailing={<Badge tone="info">3</Badge>}
            >
              <p className="type-body text-content-subtle">
                Раскрытие — один жест: и панель, и шеврон тратят рунг `reveal`.
              </p>
            </CollapsibleCard>
          </div>
        </Cell>
      </Row>

      <Row label="SurfHover" hint="строка, под которой припаркованы действия">
        <Cell caption="в покое">
          <div className="w-panel max-w-full">
            <SurfHover
              actions={
                <>
                  <IconButton size="sm" tone="primary" aria-label="Изменить">
                    <Icon name="pencil" size={14} />
                  </IconButton>
                  <IconButton size="sm" tone="danger" aria-label="Удалить">
                    <Icon name="trash" size={14} />
                  </IconButton>
                </>
              }
              surface={
                <div className="rounded-md border border-line bg-surface-card px-3 py-2">
                  <div className="type-body-medium">Кампания «Крипта»</div>
                  <div className="type-small">4 канала · 120 комментариев</div>
                </div>
              }
            />
          </div>
        </Cell>
        <Cell caption="открыт">
          <div className="w-panel max-w-full" data-catalog="surf-hover-open">
            <SurfHover
              open
              actions={
                <>
                  <IconButton size="sm" tone="primary" aria-label="Изменить">
                    <Icon name="pencil" size={14} />
                  </IconButton>
                  <IconButton size="sm" tone="danger" aria-label="Удалить">
                    <Icon name="trash" size={14} />
                  </IconButton>
                </>
              }
              surface={
                <div className="rounded-md border border-line bg-surface-card px-3 py-2">
                  <div className="type-body-medium">Кампания «Крипта»</div>
                  <div className="type-small">4 канала · 120 комментариев</div>
                </div>
              }
            />
          </div>
        </Cell>
      </Row>

      <Row label="DataTable" hint="ниже 880px превращается в карточки">
        <Cell caption="таблица" scrollable>
          <div className="w-table max-w-full">
            <DataTable data={ROWS} columns={COLUMNS} />
          </div>
        </Cell>
        <Cell caption="карточки">
          <div className="w-menu">
            <DataTable data={ROWS} columns={COLUMNS} />
          </div>
        </Cell>
      </Row>

      <Row label="Modal">
        <Cell caption="открыть">
          <Button
            variant="secondary"
            data-catalog="open-modal"
            onClick={() => {
              setModal(true);
            }}
          >
            Диалог
          </Button>
        </Cell>
        <Cell caption="открыть подтверждение">
          <Button
            variant="danger"
            data-catalog="open-confirm"
            onClick={() => {
              setConfirm(true);
            }}
          >
            Подтверждение
          </Button>
        </Cell>
      </Row>

      {modal && (
        <Modal
          label="Настройки прогрева"
          size="form"
          onClose={() => {
            setModal(false);
          }}
        >
          <div className="flex flex-col gap-4 p-6">
            <h3 className="type-h2">Настройки прогрева</h3>
            <p className="type-body text-content-muted">
              Диалог — та же поверхность, что карточка, только над завесой и с ловушкой Tab.
            </p>
            <div className="flex justify-end gap-2">
              <Button
                variant="ghost"
                onClick={() => {
                  setModal(false);
                }}
              >
                Отмена
              </Button>
              <Button
                variant="primary"
                onClick={() => {
                  setModal(false);
                }}
              >
                Сохранить
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {confirm && (
        <ConfirmModal
          title="Удалить аккаунт?"
          body="Аккаунт и его сессия будут удалены безвозвратно."
          confirmLabel="Удалить"
          cancelLabel="Отмена"
          onConfirm={() => {
            setConfirm(false);
          }}
          onClose={() => {
            setConfirm(false);
          }}
        />
      )}
    </Section>
  );
}
