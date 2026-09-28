// Экран 15. Месяц сеткой (docs/screens/15-month.md). Своей сетки в max-ui нет — CSS-сетка
// 7 колонок из Typography и точек категорий (D5, D23). Листание только стрелками, свайпа нет.
// Статус событий не пересчитываем — его прислал бэкенд.
import { Button, CellList, IconButton, Typography } from "@maxhub/max-ui";
import { useEffect } from "react";

import {
  addMonths,
  dayCategories,
  formatDate,
  itemsByDay,
  MONTH_RANGE,
  monthDiff,
  monthGrid,
  monthNumber,
  monthOf,
} from "../calendar";
import { useNavigation } from "../router";
import { ErrorBanner } from "../shell/ErrorBanner";
import { Skeleton } from "../shell/Skeleton";
import type { MonthCache, MonthState } from "../store";
import { texts } from "../texts";
import { CalendarHeader, ItemRow } from "./ListScreen";

const LOADING: MonthState = { items: null, loading: true, error: null };

/** «Октябрь 2026». */
export function monthTitle(month: string): string {
  return texts.month.title(texts.month.months[monthNumber(month)], month.slice(0, 4));
}

interface Props {
  today: string;
  months: MonthCache;
  /** Выбранный день; null — сегодня (по умолчанию). */
  selected: string | null;
  onSelect(day: string): void;
  /** Загрузить месяц, если его ещё нет в кеше. */
  onLoad(month: string): void;
  onRetry(month: string): void;
}

export function MonthScreen({ today, months, selected, onSelect, onLoad, onRetry }: Props) {
  const { push } = useNavigation();
  const day = selected ?? today;
  const month = monthOf(day);
  const current = monthOf(today);

  useEffect(() => {
    onLoad(month);
  }, [onLoad, month]);

  const { items, loading, error } = months[month] ?? LOADING;
  const byDay = itemsByDay(items ?? [], month);
  const offset = monthDiff(current, month);

  // Листание: 1-е число соседнего месяца, в текущем месяце — сегодня.
  const go = (n: number) => {
    const next = addMonths(month, n);
    onSelect(next === current ? today : `${next}-01`);
  };
  const prev = addMonths(month, -1);
  const next = addMonths(month, 1);
  const dayItems = byDay.get(day) ?? [];
  const monthEmpty = items !== null && byDay.size === 0;
  // «+ Задача» (#93): дата выбранного дня, в том числе прошедшего — задача задним числом,
  // без напоминания (D32, #108).
  const addTask = () => push({ name: "task", draft: false, date: day, from: "month" });

  return (
    <div className="screen screen--with-bar">
      <CalendarHeader tab="month" />
      {error && <ErrorBanner kind={error} onRetry={() => onRetry(month)} retrying={loading} />}
      <section className="month" aria-busy={loading || undefined}>
        <div className="month-nav">
          <IconButton
            size="xsmall"
            variant="secondary"
            aria-label={monthTitle(prev)}
            disabled={offset <= -MONTH_RANGE}
            onClick={() => go(-1)}
          >
            <span className="month-nav__arrow" aria-hidden="true">
              ‹
            </span>
          </IconButton>
          <Typography.Title variant="medium" className="month-title">
            {monthTitle(month)}
          </Typography.Title>
          <IconButton
            size="xsmall"
            variant="secondary"
            aria-label={monthTitle(next)}
            disabled={offset >= MONTH_RANGE}
            onClick={() => go(1)}
          >
            <span className="month-nav__arrow" aria-hidden="true">
              ›
            </span>
          </IconButton>
          <Button size="xsmall" variant="secondary" onClick={() => onSelect(today)}>
            {texts.month.today}
          </Button>
        </div>
        <div className="month-grid" aria-label={monthTitle(month)} role="group">
          {texts.month.weekdays.map((w) => (
            <Typography.Label key={w} className="month-weekday" aria-hidden="true">
              {w}
            </Typography.Label>
          ))}
          {monthGrid(month).map((date, i) => {
            if (date === null) return <span key={`blank-${i}`} aria-hidden="true" />;
            const categories = dayCategories(byDay.get(date) ?? []);
            const isSelected = date === day;
            const className = [
              "month-day",
              date === today && "month-day--today",
              isSelected && "month-day--selected",
            ]
              .filter(Boolean)
              .join(" ");
            const label = [
              formatDate(date, today),
              ...categories.map((c) => texts.category[c]),
            ].join(", ");
            return (
              <button
                key={date}
                type="button"
                className={className}
                aria-pressed={isSelected}
                aria-current={date === today ? "date" : undefined}
                aria-label={label}
                onClick={() => onSelect(date)}
              >
                <Typography.Body className="month-day__num">
                  {Number(date.slice(8))}
                </Typography.Body>
                <span className="month-day__dots" aria-hidden="true">
                  {categories.map((c) => (
                    <span key={c} className={`dot dot--${c}`} data-category={c} />
                  ))}
                </span>
              </button>
            );
          })}
        </div>
      </section>
      {items === null && loading && <Skeleton />}
      {monthEmpty && (
        <section className="empty">
          <Typography.Headline>
            {texts.month.monthEmpty(texts.month.monthsIn[monthNumber(month)])}
          </Typography.Headline>
          <Button size="large" stretched onClick={addTask}>
            {texts.month.addTask}
          </Button>
        </section>
      )}
      {items !== null && !monthEmpty && dayItems.length === 0 && (
        <Typography.Body className="month-day-empty">{texts.month.dayEmpty}</Typography.Body>
      )}
      {dayItems.length > 0 && (
        <CellList mode="island">
          {dayItems.map((item) => (
            <ItemRow key={`${item.type}_${item.id}`} item={item} today={today} source="month" />
          ))}
        </CellList>
      )}
      {/* Кнопка всегда видна, кроме «месяц пуст» — там она уже в блоке empty (не дублируем, #93). */}
      {!monthEmpty && (
        <div className="bottom-bar">
          <Button size="large" stretched onClick={addTask}>
            {texts.month.addTask}
          </Button>
        </div>
      )}
    </div>
  );
}
