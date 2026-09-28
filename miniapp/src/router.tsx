// Роутер экранов на состоянии (без зависимостей): стек маршрутов.
// Нижний элемент стека — вкладка («Список» | «Месяц»), выше — экраны с кнопкой «Назад».
import { createContext, useContext, useEffect } from "react";

import type { MaxBackButton } from "./bridge";
import type { ItemType } from "./types";

export type Tab = "list" | "month";
/** `share` — задача, только что добавленная из приглашения «Поделиться» (SHARE). */
export type CardSource = "list" | "month" | "bot" | "share";

export type Route =
  | { name: "list" }
  | { name: "month" }
  | { name: "card"; itemType: ItemType; id: number; source: CardSource }
  /**
   * Форма 17: новая (`draft` — с черновиком из бота) или правка задачи `taskId`.
   * `date` — дата по умолчанию (экран 15, выбранный день). `from: "month"` — открыта кнопкой
   * «+ Задача» на 15: после сохранения новой задачи возврат идёт на 15, а не на 14 (#93).
   */
  | { name: "task"; draft: boolean; taskId?: number; date?: string; from?: "month" }
  /** Экран 19 — профиль и «Пересобрать», по нажатию на шапку 14 и 15. */
  | { name: "profile" }
  /** Экран 13 — настройки напоминаний: со строки экрана 19 или из бота (`start_param=settings`). */
  | { name: "settings"; source: SettingsSource };

export type SettingsSource = "profile" | "bot";

export type NavAction =
  { type: "push"; route: Route } | { type: "back" } | { type: "tab"; tab: Tab };

export function navReducer(stack: Route[], action: NavAction): Route[] {
  switch (action.type) {
    case "push":
      return [...stack, action.route];
    case "back":
      return stack.length > 1 ? stack.slice(0, -1) : stack;
    case "tab":
      return [{ name: action.tab }];
  }
}

/** Куда ведёт start_param из бота (docs/screens/16-card.md, 17-task-form.md, экран 13 — #85). */
export type StartTarget =
  | { kind: "item"; itemType: ItemType; id: number; source?: CardSource }
  | { kind: "task_draft" }
  | { kind: "settings" }
  /** Приглашение «Поделиться» (SHARE): `share_<code>`, код — алфавит startapp. */
  | { kind: "share"; code: string }
  | null;

const ITEM_PARAM = /^item_(obligation|task)_(\d+)$/;
/**
 * «Изменить» (экран 9) и «Выбрать дату» (экран 10): `task_draft_<id>` (#96). Свой ли это черновик,
 * решает бэкенд (`/api/me` → `draft` / `draft_stale`); голый `task_draft` — кнопки до #96.
 * Тот же формат, что `_DRAFT_ID_PARAM` в backend/app/api/routes.py.
 */
const DRAFT_PARAM = /^task_draft(_[0-9a-z]{1,32})?$/;
/** Код не проверяем: битый (обрезали при копировании) бэкенд отклонит 404 — экран «ссылка недействительна». */
const SHARE_PARAM = /^share_(.*)$/;

export function parseStartParam(raw: string | null): StartTarget {
  if (!raw) return null;
  if (DRAFT_PARAM.test(raw)) return { kind: "task_draft" };
  if (raw === "settings") return { kind: "settings" };
  const share = SHARE_PARAM.exec(raw);
  if (share) return { kind: "share", code: share[1] };
  const m = ITEM_PARAM.exec(raw);
  if (!m) return null;
  const id = Number(m[2]);
  return Number.isSafeInteger(id) && id > 0
    ? { kind: "item", itemType: m[1] as ItemType, id }
    : null;
}

/**
 * Начальный стек. Из бота (`item_*`, `task_draft`, `settings`) — список 14 и поверх него
 * карточка 16, форма 17 или настройки 13: «Назад» ведёт на 14, а не на запомненную вкладку.
 * Приглашение (`share`) к этому моменту уже отработал экран ShareScreen — открывается вкладка.
 */
export function initialStack(target: StartTarget, tab: Tab): Route[] {
  if (target?.kind === "item") {
    return [
      { name: "list" },
      { name: "card", itemType: target.itemType, id: target.id, source: target.source ?? "bot" },
    ];
  }
  if (target?.kind === "task_draft") return [{ name: "list" }, { name: "task", draft: true }];
  if (target?.kind === "settings") return [{ name: "list" }, { name: "settings", source: "bot" }];
  return [{ name: tab }];
}

const TAB_KEY = "calendar.tab";

export function loadTab(): Tab {
  try {
    return localStorage.getItem(TAB_KEY) === "month" ? "month" : "list";
  } catch {
    return "list";
  }
}

export function saveTab(tab: Tab): void {
  try {
    localStorage.setItem(TAB_KEY, tab);
  } catch {
    // localStorage недоступен (приватный режим, запрет в WebView) — режим просто не запомнится.
  }
}

export interface Navigation {
  route: Route;
  depth: number;
  push(route: Route): void;
  back(): void;
  switchTab(tab: Tab): void;
  /** На экран 14 со сбросом стека; запомненный режим «Список | Месяц» не меняется. */
  home(): void;
}

export const NavigationContext = createContext<Navigation | null>(null);

export function useNavigation(): Navigation {
  const nav = useContext(NavigationContext);
  if (!nav) throw new Error("useNavigation вне NavigationContext");
  return nav;
}

/** BackButton MAX на неглавных экранах: show + onClick, при уходе — offClick + hide. */
export function useBackButton(button: MaxBackButton | null, active: boolean, onBack: () => void) {
  useEffect(() => {
    if (!button || !active) return;
    button.show();
    button.onClick(onBack);
    return () => {
      button.offClick(onBack);
      button.hide();
    };
  }, [button, active, onBack]);
}
