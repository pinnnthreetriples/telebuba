import { useTranslation } from 'react-i18next';

import { CollapsibleCard, Icon } from '@/shared/ui';

// Сводка замечаний: сколько причин мешает запуску — до того, как оператор дойдёт до
// кнопки, которая ими упирается.
//
// Причины считает `launchBlockers`, и считает их один раз на странице: карточка получает
// готовый список, а не campaign+roster+targets+roles+steps, чтобы «сколько замечаний в
// сайдбаре» и «почему кнопка серая» не могли разойтись.
//
// РАСКРЫТА по умолчанию, хотя макет просит в сайдбаре одну строку. Причина не в
// упрямстве: прежняя карточка запуска перечисляла ВСЕ причины разом, и это было решение,
// а не небрежность — «починил одну, уперся в следующую» ровно тот цикл, ради конца
// которого список и заведён. Свёрнутый по умолчанию, он прячет за клик то, что раньше
// было видно, поэтому свёртка осталась возможностью, а не состоянием по умолчанию.
//
// Пустой список не рисует ничего. Плашка «замечаний нет» была бы третьим местом, где
// сказано одно и то же: готовность уже говорят зелёное уведомление конвейера и
// доступная кнопка запуска.
export function ChecksBanner({ blockers }: { blockers: string[] }) {
  const { t } = useTranslation();
  if (blockers.length === 0) return null;
  return (
    <CollapsibleCard
      defaultOpen
      label={t('neuroshilling.checks.title', { count: blockers.length })}
      wrapperClassName="rounded-lg border border-warning-line bg-warning-tint"
      headerClassName="px-4 py-3"
      bodyClassName="px-4 pb-4"
      // Плитка `warning` на подложке того же тона: знак стоит голым, не залитым кружком.
      // Кружок был `warning-deep` на `warning-line` и мерил 4.32:1 против пола в 4.5 —
      // `line` это краска РАМКИ, и роли «чернила на рамке» в системе нет. На самой
      // плашке та же краска проходит, и это единственная пара, которую
      // `contrast.test.ts` для предупреждения знает.
      icon={<Icon name="alert-triangle" size={18} />}
      tone="warning"
      title={
        <span className="text-warning-deep">
          {t('neuroshilling.checks.title', { count: blockers.length })}
        </span>
      }
      subtitle={<span className="text-warning-deep">{t('neuroshilling.checks.subtitle')}</span>}
    >
      <ul className="flex list-none flex-col gap-1 type-small text-warning-deep">
        {blockers.map((reason) => (
          <li key={reason}>· {reason}</li>
        ))}
      </ul>
    </CollapsibleCard>
  );
}
