// Плашка ошибки с «Повторить» (product.md, «Четыре состояния»). Уже загруженные данные
// экран оставляет под ней — плашка их не заменяет.
import { Button, Typography } from "@maxhub/max-ui";

import type { ErrorKind } from "../data/http";
import { texts } from "../texts";

export function errorText(kind: ErrorKind): string {
  if (kind === "unauthorized") return texts.common.reopen;
  if (kind === "server") return texts.common.serviceError;
  return texts.common.error;
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
