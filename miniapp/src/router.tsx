// Роутер экранов на состоянии (без зависимостей): стек маршрутов.
// Нижний элемент стека — вкладка («Список» | «Месяц»), выше — экраны с кнопкой «Назад».
import { createContext, useContext, useEffect } from "react";

import type { MaxBackButton } from "./bridge";
import type { ItemType } from "./types";

export type Tab = "list" | "month";
export type CardSource = "list" | "month" | "bot";

export type Route =
  | { name: "list" }
  | { name: "month" }
  | { name: "card"; itemType: ItemType; id: number; source: CardSource }
  /** Форма 17: новая (`draft` — с черновиком из бота) или правка задачи `taskId`. */
  | { name: "task"; draft: boolean; taskId?: number };

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

/** Куда ведёт start_param из бота (docs/screens/16-card.md, 17-task-form.md). */
export type StartTarget =
  { kind: "item"; itemType: ItemType; id: number } | { kind: "task_draft" } | null;

const ITEM_PARAM = /^item_(obligation|task)_(\d+)$/;

export function parseStartParam(raw: string | null): StartTarget {
  if (!raw) return null;
  if (raw === "task_draft") return { kind: "task_draft" };
  const m = ITEM_PARAM.exec(raw);
  if (!m) return null;
  const id = Number(m[2]);
  return Number.isSafeInteger(id) && id > 0
    ? { kind: "item", itemType: m[1] as ItemType, id }
    : null;
}

/**
 * Начальный стек. Из бота (`item_*`, `task_draft`) — список 14 и поверх него карточка 16
 * или форма 17: «Назад» ведёт на 14, а не на запомненную вкладку.
 */
export function initialStack(target: StartTarget, tab: Tab): Route[] {
  if (target?.kind === "item") {
    return [
      { name: "list" },
      { name: "card", itemType: target.itemType, id: target.id, source: "bot" },
    ];
  }
  if (target?.kind === "task_draft") return [{ name: "list" }, { name: "task", draft: true }];
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
