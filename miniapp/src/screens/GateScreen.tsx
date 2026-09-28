// Экран 18. Мини-приложение открыли не через бота (docs/screens/18-gate.md).
// 401 показывает ту же заглушку без слов об ошибке авторизации.
import { Button, Panel, Spinner, Typography } from "@maxhub/max-ui";

import { getBotUrl, openBotChat } from "../bridge";
import type { ErrorKind } from "../data/http";
import { ErrorBanner } from "../shell/ErrorBanner";
import { texts } from "../texts";

interface Props {
  loading: boolean;
  /** Ошибка сети/сервиса при /api/me. 401 сюда не передаётся. */
  error: ErrorKind | null;
  retrying?: boolean;
  onRetry: () => void;
  /** Новый пользователь только что добавил срок из приглашения (SHARE): объясняем, где он. */
  added?: boolean;
}

export function GateScreen({ loading, error, retrying = false, onRetry, added = false }: Props) {
  if (loading) {
    return (
      <Panel centeredX centeredY className="screen-center" aria-busy="true">
        <Spinner />
      </Panel>
    );
  }
  const botUrl = getBotUrl();
  return (
    <div className="screen">
      {error && <ErrorBanner kind={error} onRetry={onRetry} retrying={retrying} />}
      <Panel centeredX centeredY className="gate">
        {added && <Typography.Body className="gate-added">{texts.share.gateAdded}</Typography.Body>}
        <Typography.Headline>{texts.gate.title}</Typography.Headline>
        <Typography.Body>{texts.gate.text}</Typography.Body>
        {/* Адреса бота нет (VITE_BOT_URL пуст) — кнопку не показываем, как D13. */}
        {botUrl && (
          <Button size="large" stretched onClick={() => openBotChat(botUrl)}>
            {texts.gate.openChat}
          </Button>
        )}
      </Panel>
    </div>
  );
}
