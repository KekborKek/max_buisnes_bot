// Экран 16. Карточка обязательства или своей задачи (docs/screens/16-card.md).
// Статус не пересчитываем: после отметки показываем карточку, которую вернул бэкенд.
// События item_done / item_undone / wrong_date_reported пишет бэкенд; здесь — только
// item_card_opened и error.
import { Button, CellHeader, CellList, CellSimple, Typography } from "@maxhub/max-ui";
import { type ReactNode, useEffect, useState } from "react";

import { openExternalLink } from "../bridge";
import { dateIn, formatCardDate, formatDate, formatNumericDate } from "../calendar";
import { errorKind, type ErrorKind, isNotFound } from "../data/http";
import type { DataSource } from "../data/source";
import { type Route, useNavigation } from "../router";
import { ErrorBanner } from "../shell/ErrorBanner";
import { Skeleton } from "../shell/Skeleton";
import { useToast } from "../shell/Toast";
import { useConfirmPress } from "../shell/useConfirmPress";
import { findItem, itemKey, useCalendarStore } from "../store";
import { texts } from "../texts";
import type { CalendarItem, ItemCard, ObligationCard } from "../types";

type CardRoute = Extract<Route, { name: "card" }>;
type Action = "done" | "undo" | "report" | "delete";

interface Props {
  source: DataSource;
  route: CardRoute;
  /** «Сегодня» и пояс пользователя — только для форматирования дат, не для статуса. */
  today: string;
  timezone: string;
}

const quiet = (p: Promise<unknown>) => void p.catch(() => undefined);

/**
 * Маршруты, для которых уже отправлен item_card_opened. Возврат на карточку из формы 17
 * (тот же объект маршрута в стеке) и повторный эффект StrictMode событие не дублируют.
 */
const openedRoutes = new WeakSet<CardRoute>();

function Banner({ children }: { children: ReactNode }) {
  return (
    <div className="card-banner">
      <Typography.Body>{children}</Typography.Body>
    </div>
  );
}

function ObligationDetails({ card }: { card: ObligationCard }) {
  return (
    <>
      <CellList mode="island" header={<CellHeader>{texts.card.sectionHowto}</CellHeader>}>
        {card.howto_steps.map((step, i) => (
          <CellSimple
            key={i}
            before={<Typography.Label className="card-step">{i + 1}.</Typography.Label>}
            title={step}
            innerClassNames={{ title: "card-wrap" }}
          />
        ))}
      </CellList>
      <CellList mode="island" header={<CellHeader>{texts.card.sectionPenalty}</CellHeader>}>
        <CellSimple title={card.penalty_text} innerClassNames={{ title: "card-wrap" }} />
      </CellList>
      <CellList mode="island" header={<CellHeader>{texts.card.sectionBasis}</CellHeader>}>
        <CellSimple
          title={card.norm}
          subtitle={texts.card.checked(formatNumericDate(card.last_checked_at))}
          innerClassNames={{ title: "card-wrap" }}
        />
        <CellSimple
          role="link"
          tabIndex={0}
          className="row"
          title={texts.card.source}
          showChevron
          onClick={() => openExternalLink(card.source_url)}
          onKeyDown={(e) => {
            if (e.key === "Enter") openExternalLink(card.source_url);
          }}
        />
      </CellList>
      <Typography.Label className="card-footer">{texts.card.disclaimerAdvice}</Typography.Label>
    </>
  );
}

export function CardScreen({ source, route, today, timezone }: Props) {
  const { itemType, id } = route;
  const nav = useNavigation();
  const toast = useToast();
  const { items, cards, upsert, remove } = useCalendarStore();

  // Карточка, открытая раньше за этот запуск, видна сразу; свежая придёт запросом.
  const [card, setCard] = useState<ItemCard | null>(() => cards[itemKey(itemType, id)] ?? null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<ErrorKind | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState<Action | null>(null);
  const [actionError, setActionError] = useState<{ kind: ErrorKind; retry: () => void } | null>(
    null,
  );
  const [reported, setReported] = useState(false);
  const confirmDelete = useConfirmPress();

  useEffect(() => {
    if (openedRoutes.has(route)) return;
    openedRoutes.add(route);
    quiet(
      source.track("item_card_opened", { item_id: id, item_type: itemType, source: route.source }),
    );
  }, [source, route, id, itemType]);

  useEffect(() => {
    let alive = true;
    source.item(itemType, id).then(
      (result) => {
        if (!alive) return;
        setCard(result);
        setLoading(false);
        setLoadError(null);
        upsert(result);
      },
      (e: unknown) => {
        if (!alive) return;
        setLoading(false);
        if (isNotFound(e)) {
          setNotFound(true);
          remove(itemType, id);
          return;
        }
        const kind = errorKind(e);
        setLoadError(kind);
        quiet(source.track("error", { where: "card", kind }));
      },
    );
    return () => {
      alive = false;
    };
  }, [source, itemType, id, attempt, upsert, remove]);

  const retryLoad = () => {
    setLoading(true);
    setAttempt((n) => n + 1);
  };

  const run = (action: Action, request: () => Promise<void>) => {
    setBusy(action);
    setActionError(null);
    request().then(
      () => setBusy(null),
      (e: unknown) => {
        setBusy(null);
        if (isNotFound(e)) {
          setNotFound(true);
          remove(itemType, id);
          return;
        }
        const kind = errorKind(e);
        // Статус остаётся прежним: карточку не трогаем, только плашка.
        setActionError({ kind, retry: () => run(action, request) });
        quiet(source.track("error", { where: `card_${action}`, kind }));
      },
    );
  };

  const apply = (next: ItemCard) => {
    setCard(next);
    upsert(next);
  };

  if (notFound) {
    return (
      <div className="screen">
        <section className="empty">
          <Typography.Headline>{texts.card.notFound}</Typography.Headline>
          <Button size="large" stretched onClick={nav.home}>
            {texts.card.backToList}
          </Button>
        </section>
      </div>
    );
  }

  const head: CalendarItem | null = card ?? findItem(items, itemType, id);
  const done = card?.status === "done";
  const error = actionError ?? (loadError ? { kind: loadError, retry: retryLoad } : null);

  const markDone = () =>
    run(done ? "undo" : "done", () =>
      (done ? source.undoDone(itemType, id) : source.markDone(itemType, id)).then(apply),
    );
  const report = () =>
    run("report", () =>
      source.reportWrongDate(id).then(() => {
        setReported(true);
        toast(texts.card.wrongDateThanks);
      }),
    );
  const onDelete = () => {
    if (!confirmDelete.armed) {
      confirmDelete.arm();
      return;
    }
    confirmDelete.reset();
    run("delete", () =>
      source.deleteTask(id).then(() => {
        remove("task", id);
        nav.home();
        toast(texts.card.deleted);
      }),
    );
  };

  let statusBanner = null;
  if (head?.status === "overdue") {
    statusBanner = <Banner>{texts.card.overdueBanner(formatDate(head.due_date, today))}</Banner>;
  } else if (head?.status === "done" && head.done_at) {
    statusBanner = (
      <Banner>{texts.card.doneBanner(formatDate(dateIn(timezone, head.done_at), today))}</Banner>
    );
  }

  return (
    <div className="screen">
      {error && (
        <ErrorBanner
          kind={error.kind}
          onRetry={error.retry}
          retrying={actionError ? busy !== null : loading}
        />
      )}
      {statusBanner}
      {head && (
        <header className="card-head">
          <Typography.Headline>{head.title}</Typography.Headline>
          <Typography.Body className="card-meta">
            {formatCardDate(head.due_date, today)} · {texts.status[head.status]} ·{" "}
            <span className="row-category">
              <span className={`dot dot--${head.category}`} aria-hidden="true" />
              {texts.category[head.category]}
            </span>
          </Typography.Body>
          {head.original_date !== head.due_date && (
            <Typography.Label className="card-note">
              {texts.card.shifted(formatDate(head.original_date, today))}
            </Typography.Label>
          )}
          {card?.type === "task" && (card.status === "upcoming" || card.status === "today") && (
            <Typography.Label className="card-note">
              {texts.card.remindAt(
                texts.card.remindWhen[card.remind_offset_days],
                card.remind_hour,
              )}
            </Typography.Label>
          )}
          {/* disclaimer.mark — только у обязательства: текст про оплату и отчётность. */}
          {card?.type === "obligation" && done && (
            <Typography.Body className="card-disclaimer">
              {texts.card.disclaimerMark}
            </Typography.Body>
          )}
        </header>
      )}
      {!card && loading && <Skeleton />}
      {card && (
        <div className="card-actions">
          <Button
            size="large"
            stretched
            variant={done ? "secondary" : "primary"}
            loading={busy === "done" || busy === "undo"}
            disabled={busy !== null}
            onClick={markDone}
          >
            {done ? texts.card.undoDone : texts.card.markDone}
          </Button>
          {card.type === "obligation" ? (
            <Button
              size="large"
              stretched
              variant="secondary"
              loading={busy === "report"}
              disabled={busy !== null || reported}
              onClick={report}
            >
              {texts.card.wrongDate}
            </Button>
          ) : (
            <div className="card-actions__row">
              <Button
                size="large"
                stretched
                variant="secondary"
                disabled={busy !== null}
                onClick={() => nav.push({ name: "task", draft: false, taskId: id })}
              >
                {texts.card.reschedule}
              </Button>
              <Button
                size="large"
                stretched
                variant={confirmDelete.armed ? "destructive" : "secondary"}
                loading={busy === "delete"}
                disabled={busy !== null}
                onClick={onDelete}
              >
                {confirmDelete.armed ? texts.card.deleteConfirm : texts.card.delete}
              </Button>
            </div>
          )}
        </div>
      )}
      {card?.type === "obligation" && <ObligationDetails card={card} />}
    </div>
  );
}
