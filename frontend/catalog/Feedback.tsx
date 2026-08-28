// Обратная связь: Badge, Notice, StatusIcon, FeedbackMark, HelpHint.
//
// Badge и Notice носят один набор тонов — neutral, primary, success, warning, danger, —
// и стоят рядом именно для этого: тон означает одно и то же в строчной плашке и в
// блочном уведомлении. `neutral` у Notice нет, и это видно здесь, а не в чьей-то памяти.
import { Badge, FeedbackMark, HelpHint, HintBubble, Notice, StatusIcon } from '@/shared/ui';

import { Cell, Row, Section } from './Frame';

const BADGE_TONES = ['neutral', 'primary', 'success', 'warning', 'danger'] as const;
const BADGE_SIZES = ['md', 'sm', 'xs'] as const;
const NOTICE_TONES = ['primary', 'success', 'warning', 'danger'] as const;

export function Feedback() {
  return (
    <Section
      id="feedback"
      title="Обратная связь"
      note="Один набор тонов на строчную плашку и на блочное уведомление. Badge несёт neutral, Notice — нет: у уведомления без тона нет смысла, который оно сообщает."
    >
      {BADGE_SIZES.map((size) => (
        <Row key={size} label={`Badge · ${size}`}>
          {BADGE_TONES.map((tone) => (
            <Cell key={tone} caption={tone}>
              <Badge tone={tone} size={size}>
                Прогрет
              </Badge>
            </Cell>
          ))}
        </Row>
      ))}

      <Row label="Badge · с точкой" hint="точка красится bg-current">
        {BADGE_TONES.map((tone) => (
          <Cell key={tone} caption={tone}>
            <Badge tone={tone} dot>
              Активен
            </Badge>
          </Cell>
        ))}
      </Row>

      <Row label="Notice · с рамкой">
        {NOTICE_TONES.map((tone) => (
          <Cell key={tone} caption={tone}>
            <div className="w-panel">
              <Notice tone={tone}>
                Прокси отвечает медленнее порога — аккаунт снят с очереди.
              </Notice>
            </div>
          </Cell>
        ))}
      </Row>

      <Row label="Notice · без рамки" hint="для вложенных в панель">
        {NOTICE_TONES.map((tone) => (
          <Cell key={tone} caption={tone}>
            <div className="w-panel">
              <Notice tone={tone} bordered={false}>
                Прокси отвечает медленнее порога.
              </Notice>
            </div>
          </Cell>
        ))}
      </Row>

      <Row label="StatusIcon">
        <Cell caption="ok">
          <StatusIcon kind="ok" />
        </Cell>
        <Cell caption="err">
          <StatusIcon kind="err" />
        </Cell>
      </Row>

      <Row label="FeedbackMark" hint="пусто, пока действия не было">
        <Cell caption="без результата">
          <FeedbackMark />
        </Cell>
        <Cell caption="ok">
          <FeedbackMark result="ok" />
        </Cell>
        <Cell caption="err">
          <FeedbackMark result="err" />
        </Cell>
      </Row>

      <Row label="HelpHint" hint="раскрывается по наведению и по фокусу">
        <Cell caption="в покое">
          <HelpHint text="Сколько действий аккаунт делает за сутки." />
        </Cell>
        <Cell caption="hover" probe="hover">
          <HelpHint
            text="Сколько действий аккаунт делает за сутки."
            example="20 — прогрев, 60 — рабочий режим"
          />
        </Cell>
        <Cell caption="focus-within" probe="focus">
          <HelpHint text="Сколько действий аккаунт делает за сутки." />
        </Cell>
        <Cell caption="HintBubble">
          <div className="w-tip">
            <HintBubble
              text="Сколько действий аккаунт делает за сутки."
              example="20 — прогрев, 60 — рабочий режим"
            />
          </div>
        </Cell>
      </Row>
    </Section>
  );
}
