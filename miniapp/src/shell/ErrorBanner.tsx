// Плашка ошибки с «Повторить» (product.md, «Четыре состояния»). Уже загруженные данные
// экран оставляет под ней — плашка их не заменяет.
import { Button, Typography } from "@maxhub/max-ui";

import type { ErrorKind } from "../data/http";
import { texts } from "../texts";

/** В спеке одна формулировка для любой ошибки (сеть, таймаут, 5xx); отдельно — только 401. */
export function errorText(kind: ErrorKind): string {
  return kind === "unauthorized" ? texts.common.reopen : texts.common.error;
}

export function ErrorBanner({
  kind,
  onRetry,
  retrying = false,
}: {
  kind: ErrorKind;
  onRetry: () => void;
  retrying?: boolean;
}) {
  return (
    <div className="error-banner" role="alert">
      <Typography.Body>{errorText(kind)}</Typography.Body>
      {/* 401: повтор не поможет, нужно открыть мини-апп из бота заново. */}
      {kind !== "unauthorized" && (
        <Button size="small" variant="secondary" loading={retrying} onClick={onRetry}>
          {texts.common.retry}
        </Button>
      )}
    </div>
  );
}
