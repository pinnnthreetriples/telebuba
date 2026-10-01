import { Button } from '../src/shared/ui';

const SIZES = [
  { size: 'xs', height: '28 px', label: 'Проверить', use: 'Короткое действие в плотной строке' },
  { size: 'sm', height: '32 px', label: 'Добавить', use: 'Действие внутри карточки' },
  { size: 'md', height: '36 px', label: 'Сохранить', use: 'Форма или подвал диалога' },
  { size: 'lg', height: '44 px', label: 'Начать прогрев', use: 'Крупная цель для касания' },
] as const;

export function ButtonSizingGuide() {
  return (
    <div className="rounded-card border border-info-line bg-info-tint p-lg">
      <h3 className="type-card-title">Как выбирать размер кнопки</h3>
      <p className="mt-tight type-prose">
        Высота отвечает за место действия, цвет — за его смысл. Эти оси показаны отдельно: сочетание
        размера и заливки не создаёт новый вид кнопки.
      </p>
      <div className="mt-lg grid gap-lg md:grid-cols-2">
        {SIZES.map(({ size, height, label, use }) => (
          <div key={size} className="flex flex-wrap items-center gap-md">
            <Button size={size} variant={size === 'lg' ? 'primary' : 'secondary'}>
              {label}
            </Button>
            <span className="type-caption">
              <strong>
                {size} · {height}
              </strong>{' '}
              — {use}
            </span>
          </div>
        ))}
      </div>
      <div className="mt-lg border-t border-info-line pt-md">
        <p className="mb-sm type-caption">
          <strong>fullWidth</strong> — ширина на всю форму при высоте md. Это не отдельный размер.
        </p>
        <div className="w-menu max-w-full">
          <Button fullWidth variant="primary" weight="medium">
            Войти
          </Button>
        </div>
      </div>
    </div>
  );
}
