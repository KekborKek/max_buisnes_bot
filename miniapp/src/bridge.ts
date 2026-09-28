// Типизированный доступ к MAX Bridge (window.WebApp).
// Методы — только из docs/max-api-notes.md, не выдумывать по аналогии с Telegram.

export interface MaxBackButton {
  show(): void;
  hide(): void;
  onClick(cb: () => void): void;
  offClick(cb: () => void): void;
}

/** Параметры `shareMaxContent` / `shareContent` в режиме «текст и ссылка» (dev.max.ru/docs/webapps/bridge). */
export interface MaxShareParams {
  text?: string;
  link?: string;
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
  /** Поделиться в чат MAX: все платформы, Bridge проверяет клик пользователя. */
  shareMaxContent?: (params: MaxShareParams) => Promise<void> | void;
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

/**
 * Диплинк мини-аппа с параметром запуска: `https://max.ru/<botName>?startapp=<param>` —
 * `param` придёт в `initDataUnsafe.start_param` (docs/max-api-notes.md, «Диплинки»).
 * Адрес бота не разбирается — пустая строка.
 */
export function miniappStartUrl(botUrl: string, param: string): string {
  if (!botUrl) return "";
  try {
    const url = new URL(botUrl);
    url.searchParams.set("startapp", param);
    return url.toString();
  } catch {
    return "";
  }
}

/** Скопировать текст: Clipboard API, если его нет или он запрещён в WebView — execCommand. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // запрет в WebView или нет жеста — пробуем старый способ
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

export type ShareResult = "shared" | "cancelled" | "copied" | "failed";

/**
 * «Поделиться» (SHARE). В MAX — `shareMaxContent({text, link})`: окно выбора чата MAX на всех
 * платформах. Bridge проверяет клик пользователя, поэтому метод вызывается синхронно, до
 * первого await, — звать эту функцию надо прямо из обработчика нажатия.
 * Метода нет (вне MAX) — текст со ссылкой копируется в буфер.
 * Метод отклонил вызов — `"cancelled"`, без тоста и без записи в буфер: так бывает при отмене
 * выбора чата и при нажатии без жеста (ссылка ещё грузилась) — повторное нажатие сработает.
 * `shareContent` не берём: он только для iOS/Android и отдаёт ссылку в чужие приложения.
 */
export async function shareToChat(text: string, link: string): Promise<ShareResult> {
  const wa = getWebApp();
  if (wa?.shareMaxContent) {
    // Вызов как метода объекта: реализация Bridge может опираться на this.
    try {
      await wa.shareMaxContent({ text, link });
      return "shared";
    } catch {
      return "cancelled";
    }
  }
  return (await copyText(`${text}\n${link}`)) ? "copied" : "failed";
}
