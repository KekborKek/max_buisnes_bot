// «Поделиться сроком» (SHARE): мини-апп открыли по ссылке `?startapp=share_<code>`.
// Получатель видит название и дату и одним нажатием добавляет себе задачу. Профиль не нужен:
// получатель может быть новым пользователем — после «Добавить» App покажет заглушку 18.
// share_opened, share_accepted и task_created пишет бэкенд; здесь — share_declined и error.
import { Button, Panel, Spinner, Typography } from "@maxhub/max-ui";
import { useEffect, useState } from "react";

import { formatCardDate } from "../calendar";
import { errorKind, isNotFound } from "../data/http";
import type { DataSource } from "../data/source";
import { useToast } from "../shell/Toast";
import { texts } from "../texts";
import type { ShareInvite, TaskCard } from "../types";

interface Props {
  source: DataSource;
  code: string;
  /** «Сегодня» пользователя (YYYY-MM-DD) — для формата даты и подсказки о прошедшем сроке. */
  today: string;
  /** Добавили — задача получателя; «Не нужно» или ссылка недействительна — null. */
  onFinish: (task: TaskCard | null) => void;
}

type State =
  | { kind: "loading" }
  | { kind: "invalid" }
  | { kind: "failed" }
  | { kind: "ready"; invite: ShareInvite };

const quiet = (p: Promise<unknown>) => void p.catch(() => undefined);

function Alert({
  text,
  retrying,
  onRetry,
}: {
  text: string;
  retrying: boolean;
  onRetry: () => void;
}) {
  return (
    <div className="error-banner" role="alert">
      <Typography.Body>{text}</Typography.Body>
      <Button size="small" variant="secondary" loading={retrying} onClick={onRetry}>
        {texts.common.retry}
      </Button>
    </div>
  );
}

export function ShareScreen({ source, code, today, onFinish }: Props) {
  const toast = useToast();
  // Пустой код (`share_` без продолжения) — сразу «недействительна», без запроса.
  const [state, setState] = useState<State>(() =>
    code ? { kind: "loading" } : { kind: "invalid" },
  );
  const [attempt, setAttempt] = useState(0);
  const [accepting, setAccepting] = useState(false);
  const [acceptFailed, setAcceptFailed] = useState(false);

  useEffect(() => {
    if (!code) return;
    let alive = true;
    source.getShare(code).then(
      (invite) => {
        if (alive) setState({ kind: "ready", invite });
      },
      (e: unknown) => {
        if (!alive) return;
        if (isNotFound(e)) {
          setState({ kind: "invalid" });
          return;
        }
        setState({ kind: "failed" });
        quiet(source.track("error", { where: "share", kind: errorKind(e) }));
      },
    );
    return () => {
      alive = false;
    };
  }, [source, code, attempt]);

  const retryLoad = () => {
    setState({ kind: "loading" });
    setAttempt((n) => n + 1);
  };

  const accept = () => {
    setAccepting(true);
    setAcceptFailed(false);
    source.acceptShare(code).then(
      (result) => {
        setAccepting(false);
        toast(texts.share.added);
        onFinish(result.task);
      },
      (e: unknown) => {
        setAccepting(false);
        if (isNotFound(e)) {
          setState({ kind: "invalid" });
          return;
        }
        // Приглашение остаётся на экране, «Добавить» можно нажать ещё раз.
        setAcceptFailed(true);
        quiet(source.track("error", { where: "share_accept", kind: errorKind(e) }));
      },
    );
  };

  const decline = (itemType: ShareInvite["item_type"]) => {
    quiet(source.track("share_declined", { item_type: itemType }));
    onFinish(null);
  };

  if (state.kind === "loading") {
    return (
      <Panel centeredX centeredY className="screen-center" aria-busy="true">
        <Spinner />
      </Panel>
    );
  }

  if (state.kind === "invalid") {
    return (
      <div className="screen">
        <section className="empty">
          <Typography.Headline>{texts.share.invalidTitle}</Typography.Headline>
          <Typography.Body>{texts.share.invalidText}</Typography.Body>
          <Button size="large" stretched onClick={() => onFinish(null)}>
            {texts.share.toCalendar}
          </Button>
        </section>
      </div>
    );
  }

  if (state.kind === "failed") {
    return (
      <div className="screen">
        <Alert text={texts.share.loadError} retrying={false} onRetry={retryLoad} />
        <section className="empty">
          <Button size="large" stretched variant="secondary" onClick={() => onFinish(null)}>
            {texts.share.toCalendar}
          </Button>
        </section>
      </div>
    );
  }

  const { invite } = state;
  return (
    <div className="screen">
      {acceptFailed && (
        <Alert text={texts.share.acceptError} retrying={accepting} onRetry={accept} />
      )}
      <header className="card-head">
        <Typography.Label className="card-note">{texts.share.title}</Typography.Label>
        <Typography.Headline>{invite.title}</Typography.Headline>
        <Typography.Body className="card-meta">
          {formatCardDate(invite.due_date, today)}
        </Typography.Body>
        {invite.due_date < today && (
          <Typography.Label className="card-note">{texts.share.past}</Typography.Label>
        )}
      </header>
      <div className="card-actions">
        <Button size="large" stretched loading={accepting} disabled={accepting} onClick={accept}>
          {texts.share.add}
        </Button>
        <Button
          size="large"
          stretched
          variant="secondary"
          disabled={accepting}
          onClick={() => decline(invite.item_type)}
        >
          {texts.share.decline}
        </Button>
      </div>
    </div>
  );
}
