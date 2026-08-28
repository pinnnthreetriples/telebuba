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
          <div className="w-panel">
            <Card>
              <p className="type-prose">
                Карточка без шапки: белая, волосяная рамка, rounded-card.
              </p>
            </Card>
          </div>
        </Cell>
        <Cell caption="с заголовком">
          <div className="w-panel">
            <Card title="Прокси" subtitle="12 из 40 занято">
              <p className="type-prose">Заголовок и подзаголовок — роли карточки, не размеры.</p>
            </Card>
          </div>
        </Cell>
      </Row>

      <Row label="CollapsibleCard">
        <Cell caption="закрыта">
          <div className="w-panel">
            <CollapsibleCard header={<span className="type-card-title">Ограничения</span>}>
              <p className="type-prose">Тело раскрывается по клику на шапку.</p>
            </CollapsibleCard>
          </div>
        </Cell>
        <Cell caption="открыта">
          <div className="w-panel">
            <CollapsibleCard
              defaultOpen
              header={<span className="type-card-title">Ограничения</span>}
              trailing={<Badge tone="primary">3</Badge>}
            >
              <p className="type-prose">
                Раскрытие — один жест: и панель, и шеврон тратят рунг `reveal`.
              </p>
            </CollapsibleCard>
          </div>
        </Cell>
      </Row>

      <Row label="SurfHover" hint="строка, под которой припаркованы действия">
        <Cell caption="в покое">
          <div className="w-panel">
            <SurfHover
              shift={92}
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
                <div className="rounded-lg border border-line bg-surface-card px-md py-sm">
                  <div className="type-item-title">Кампания «Крипта»</div>
                  <div className="type-caption">4 канала · 120 комментариев</div>
                </div>
              }
            />
          </div>
        </Cell>
        <Cell caption="открыт">
          <div className="w-panel">
            <SurfHover
              open
              shift={92}
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
                <div className="rounded-lg border border-line bg-surface-card px-md py-sm">
                  <div className="type-item-title">Кампания «Крипта»</div>
                  <div className="type-caption">4 канала · 120 комментариев</div>
                </div>
              }
            />
          </div>
        </Cell>
      </Row>

      <Row label="DataTable" hint="ниже 880px превращается в карточки">
        <Cell caption="таблица">
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
          className="w-form"
          onClose={() => {
            setModal(false);
          }}
        >
          <div className="flex flex-col gap-lg p-xl">
            <h3 className="type-dialog-title">Настройки прогрева</h3>
            <p className="type-dialog-body">
              Диалог — та же поверхность, что карточка, только над завесой и с ловушкой Tab.
            </p>
            <div className="flex justify-end gap-sm">
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
