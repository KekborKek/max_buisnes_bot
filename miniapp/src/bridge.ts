// Типизированный доступ к MAX Bridge (window.WebApp).
// Методы — только из docs/max-api-notes.md, не выдумывать по аналогии с Telegram.

export interface MaxBackButton {
  show(): void;
  hide(): void;
  onClick(cb: () => void): void;
  offClick(cb: () => void): void;
}

export interface MaxWebApp {
  initData: string;
  initDataUnsafe: {
    user?: { user_id?: number; id?: number; first_name?: string };
    start_param?: unknown;
  };
  platform?: string;
  version?: string;
  requestContact?: () => Promise<unknown>;
  openCodeReader?: () => Promise<unknown>;
  openLink?: (url: string) => void;
  openMaxLink?: (url: string) => void;
  BackButton?: MaxBackButton;
  HapticFeedback?: unknown;
}

declare global {
  interface Window {
    WebApp?: MaxWebApp;
  }
}

/** Мы внутри MAX, если Bridge загрузился и передал initData. */
export function getWebApp(): MaxWebApp | null {
  const wa = window.WebApp;
  return wa && wa.initData ? wa : null;
}

/** initData для заголовка X-Max-Init-Data. Вне MAX (локальная разработка) — "dev". */
export function getInitData(): string {
  return getWebApp()?.initData ?? "dev";
}

/**
 * start_param запуска: в MAX — `WebApp.initDataUnsafe.start_param`;
 * вне MAX — query-параметр `?start_param=`, чтобы отлаживать диплинки на localhost:5173.
 */
export function getStartParam(): string | null {
  const wa = getWebApp();
  const raw = wa
    ? wa.initDataUnsafe?.start_param
    : new URLSearchParams(window.location.search).get("start_param");
  return typeof raw === "string" && raw ? raw : null;
}

/** Кнопка «Назад» MAX; вне MAX её нет — оболочка показывает замену. */
export function getBackButton(): MaxBackButton | null {
  return getWebApp()?.BackButton ?? null;
}

/** Ссылка на диалог с ботом (`https://max.ru/<botName>`). Пусто — кнопка «Открыть чат» скрыта. */
export function getBotUrl(): string {
  const url: unknown = import.meta.env.VITE_BOT_URL;
  return typeof url === "string" ? url.trim() : "";
}

/**
 * Диплинк в чат с ботом с параметром запуска: `https://max.ru/<botName>?start=<payload>` —
 * бот получает bot_started с payload (dev.max.ru/docs/chatbots/bots-coding/prepare, до 128
 * символов). Придёт ли bot_started, если диалог уже начат, — [сверить] на живом клиенте.
 */
export function botStartUrl(botUrl: string, payload: string): string {
  try {
    const url = new URL(botUrl);
    url.searchParams.set("start", payload);
    return url.toString();
  } catch {
    return botUrl;
  }
}

/**
 * Открыть внешнюю ссылку (источник нормы на экране 16). В MAX — `openLink(url)`
 * (docs/max-api-notes.md) [сверить: поведение на живом клиенте]; вне MAX — новая вкладка.
 */
export function openExternalLink(url: string): void {
  const wa = getWebApp();
  if (wa?.openLink) {
    wa.openLink(url);
    return;
  }
  window.open(url, "_blank", "noopener");
}

/**
 * Открыть ленту iCalendar (экран 19, «Добавить в календарь телефона»). В MAX —
 * `openLink(url)`: ссылка уходит во внешний браузер, а тот передаёт .ics календарю телефона.
 * Bridge проверяет клик пользователя (dev.max.ru/docs/webapps/bridge): без клика перехода нет,
 * поэтому звать только синхронно из обработчика нажатия, а ссылку получать заранее.
 * Передаём https: про схему webcal:// в документации openLink ничего нет [сверить].
 * Вне MAX — новая вкладка.
 */
export function openCalendarFeed(url: string): void {
  openExternalLink(url);
}

/**
 * Скопировать ссылку в буфер обмена: Clipboard API, если его нет или WebView его запретил —
 * выделение скрытого поля и `execCommand("copy")`. false — не вышло, ссылку покажут текстом.
 * В документации MAX Bridge метода копирования нет — только веб-API браузера.
 */
export async function copyLink(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // запасной путь ниже
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  } catch {
    return false;
  }
}

/**
 * Открыть диалог с ботом. В MAX — `openMaxLink(url)` [сверить: поведение на живом клиенте];
 * вне MAX или без метода — обычная ссылка.
 */
export function openBotChat(url: string): void {
  const wa = getWebApp();
  if (wa?.openMaxLink) {
    wa.openMaxLink(url);
    return;
  }
  window.open(url, "_blank", "noopener");
}
