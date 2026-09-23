// Экран 14. Список ближайших событий — главный экран (docs/screens/14-list.md).
import { Button, CellHeader, CellList, CellSimple, Typography } from "@maxhub/max-ui";

import { formatShortDate, groupSections, type SectionKey } from "../calendar";
import type { ErrorKind } from "../data/http";
import { type Tab, useNavigation } from "../router";
import { ErrorBanner } from "../shell/ErrorBanner";
import { Skeleton } from "../shell/Skeleton";
import { texts } from "../texts";
import type { CalendarItem, Profile } from "../types";

export interface CalendarState {
  items: CalendarItem[] | null;
  loading: boolean;
  error: ErrorKind | null;
}

const SECTION_TITLE: Record<SectionKey, string> = {
  overdue: texts.list.sectionOverdue,
  today: texts.list.sectionToday,
  week: texts.list.sectionWeek,
  later: texts.list.sectionLater,
};

/** Шапка экранов 14 и 15: профиль одной строкой и переключатель «Список | Месяц» (D8). */
export function CalendarHeader({ profile, tab }: { profile: Profile; tab: Tab }) {
  const { switchTab } = useNavigation();
  const employees = profile.has_employees ? texts.list.withEmployees : texts.list.withoutEmployees;
  const tabButton = (value: Tab, label: string) => (
    <Button
      size="small"
      variant={tab === value ? "primary" : "secondary"}
      aria-pressed={tab === value}
      onClick={() => switchTab(value)}
    >
      {label}
    </Button>
  );
  return (
    <header className="calendar-header">
      {/* Шапка ведёт на экран 19; пока его нет — не нажимается. */}
      <Typography.Body className="calendar-header__profile">
        {texts.list.profile(texts.regime[profile.regime] ?? texts.regime.unknown, employees)}
      </Typography.Body>
      <div className="tabs" role="group">
        {tabButton("list", texts.list.tabList)}
        {tabButton("month", texts.list.tabMonth)}
      </div>
    </header>
  );
}

function CheckIcon() {
  return (
    <svg className="check" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
      <path
        d="M4 10.5l4 4 8-9"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ItemRow({ item, today }: { item: CalendarItem; today: string }) {
  const { push } = useNavigation();
  const open = () => push({ name: "card", itemType: item.type, id: item.id, source: "list" });
  let after = null;
  if (item.status === "done") {
    after = (
      <span className="row-status row-status--done" aria-label={texts.status.done}>
        <CheckIcon />
      </span>
    );
  } else if (item.status === "overdue") {
    after = (
      <Typography.Label className="row-status row-status--overdue">
        {texts.list.statusOverdue}
      </Typography.Label>
    );
  }
  return (
    <CellSimple
      role="button"
      tabIndex={0}
      onClick={open}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      }}
      className={item.status === "done" ? "row row--done" : "row"}
      innerClassNames={{ before: "row-before", title: "row-title" }}
      before={
        <Typography.Label className="row-date">
          {formatShortDate(item.due_date, today)}
        </Typography.Label>
      }
      title={item.title}
      subtitle={
        <span className="row-category">
          <span className={`dot dot--${item.category}`} aria-hidden="true" />
          {texts.category[item.category]}
        </span>
      }
      after={after}
    />
  );
}

interface Props {
  profile: Profile;
  today: string;
  calendar: CalendarState;
  onRetry: () => void;
}

export function ListScreen({ profile, today, calendar, onRetry }: Props) {
  const { push, switchTab } = useNavigation();
  const { items, loading, error } = calendar;
  const addTask = () => push({ name: "task", draft: false });
  const isEmpty = items !== null && items.length === 0 && !error;

  return (
    <div className="screen screen--with-bar">
      <CalendarHeader profile={profile} tab="list" />
      {error && <ErrorBanner kind={error} onRetry={onRetry} retrying={loading} />}
      {items === null && loading && <Skeleton />}
      {isEmpty && (
        <section className="empty">
          <Typography.Headline>{texts.list.emptyTitle}</Typography.Headline>
          <Typography.Body>{texts.list.emptyText}</Typography.Body>
          <Button size="large" stretched onClick={addTask}>
            {texts.list.addTask}
          </Button>
          <Button size="large" stretched variant="secondary" onClick={() => switchTab("month")}>
            {texts.list.emptyMonth}
          </Button>
        </section>
      )}
      {items !== null &&
        groupSections(items, today).map((section) => (
          <CellList
            key={section.key}
            mode="island"
            header={<CellHeader>{SECTION_TITLE[section.key]}</CellHeader>}
          >
            {section.items.map((item) => (
              <ItemRow key={`${item.type}_${item.id}`} item={item} today={today} />
            ))}
          </CellList>
        ))}
      {!isEmpty && (
        <div className="bottom-bar">
          <Button size="large" stretched onClick={addTask}>
            {texts.list.addTask}
          </Button>
        </div>
      )}
    </div>
  );
}
