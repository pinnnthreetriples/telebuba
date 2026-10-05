import { Button } from '../src/shared/ui';

const SIZES = [
  { size: 'xs', height: '28 px', label: 'Проверить', use: 'Короткое действие в плотной строке' },
  { size: 'sm', height: '32 px', label: 'Добавить', use: 'Действие внутри карточки' },
  { size: 'md', height: '36 px', label: 'Сохранить', use: 'Форма или подвал диалога' },
  { size: 'lg', height: '44 px', label: 'Начать прогрев', use: 'Крупная цель для касания' },
] as const;

export function ButtonSizingGuide() {
  return (
    <div className="rounded-lg border border-info-line bg-info-tint p-4">
      <h3 className="type-h3">Как выбирать размер кнопки</h3>
      <p className="mt-2 type-body text-content-subtle">
        Высота отвечает за место действия, цвет — за его смысл. Эти оси показаны отдельно: сочетание
        размера и заливки не создаёт новый вид кнопки.
      </p>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        {SIZES.map(({ size, height, label, use }) => (
          <div key={size} className="flex flex-wrap items-center gap-3">
            <Button size={size} variant={size === 'lg' ? 'primary' : 'secondary'}>
              {label}
            </Button>
            <span className="type-small">
              <strong>
                {size} · {height}
              </strong>{' '}
              — {use}
            </span>
          </div>
        ))}
      </div>
      <div className="mt-4 border-t border-info-line pt-3">
        <p className="mb-2 type-small">
          <strong>fullWidth</strong> — ширина на всю форму при высоте md. Это не отдельный размер.
        </p>
        <div className="w-menu max-w-full">
          <Button fullWidth variant="primary" className="font-medium">
            Войти
          </Button>
        </div>
      </div>
    </div>
  );
}
