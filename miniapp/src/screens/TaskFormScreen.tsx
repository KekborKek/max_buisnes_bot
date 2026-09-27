// Экран 17. Форма своей задачи (docs/screens/17-task-form.md): четыре поля (D9),
// дата и время напоминания — нативные input type="date"/"time" (D8, TIME-FE #104).
// Дата в прошлом разрешена (D32, #108): «Напомнить» и «Время» скрыты, вместо них подсказка,
// а у новой задачи — переключатель «Уже выполнено».
// Событие task_created пишет бэкенд в POST /api/tasks.
import {
  Button,
  CellHeader,
  CellInput,
  CellList,
  CellSimple,
  Counter,
  Input,
  Radio,
  Switch,
  Typography,
} from "@maxhub/max-ui";
import { useEffect, useRef, useState } from "react";

import { addDays } from "../calendar";
import { errorKind, type ErrorKind } from "../data/http";
import type { DataSource } from "../data/source";
import { useNavigation } from "../router";
import { ErrorBanner } from "../shell/ErrorBanner";
import { useToast } from "../shell/Toast";
import { useConfirmPress } from "../shell/useConfirmPress";
import { itemKey, useCalendarStore } from "../store";
import { texts } from "../texts";
import { REMIND_OFFSETS, type RemindOffset, type TaskDraft, type TaskInput } from "../types";

export const TITLE_MAX = 60;
/** Счётчик символов появляется, когда название длиннее 50. */
export const TITLE_COUNTER_FROM = 50;

/** Время по умолчанию (D11 / контракт TIME-BE, #103, #104). */
const DEFAULT_TIME = "10:00";

const OFFSET_LABEL: Record<RemindOffset, string> = {
  0: texts.form.remind0,
  1: texts.form.remind1,
  3: texts.form.remind3,
  7: texts.form.remind7,
};

const pad2 = (n: number) => String(n).padStart(2, "0");
/** "HH:MM" для value input type="time" — в отличие от текста карточки, час тоже с нулём. */
const timeValue = (hour: number, minute: number) => `${pad2(hour)}:${pad2(minute)}`;

interface Values {
  title: string;
  date: string;
  offset: RemindOffset;
  /** "HH:MM" или "" — поле очищено (input type="time"). */
  time: string;
  /** «Уже выполнено» — уходит в запрос, только если дата в прошлом (D32). */
  done: boolean;
}

/** Дата раньше сегодняшней: напоминаний нет (D32, #108). */
export const isPast = (date: string, today: string) => date !== "" && date < today;

export interface FieldErrors {
  title: string | null;
  date: string | null;
  time: string | null;
}

/**
 * Валидация из спеки: название и дата обязательны, дата может быть в прошлом (D32);
 * время обязательно, только пока поле видно — у даты в прошлом его нет.
 */
export function validate(
  values: Pick<Values, "title" | "date" | "time">,
  today: string,
): FieldErrors {
  return {
    title: values.title.trim() === "" ? texts.form.nameRequired : null,
    date: values.date === "" ? texts.form.dateRequired : null,
    time: values.time === "" && !isPast(values.date, today) ? texts.form.timeRequired : null,
  };
}

interface Props {
  source: DataSource;
  /** «Сегодня» в поясе пользователя. */
  today: string;
  /** Черновик из бота (`start_param=task_draft` → `/api/me`). */
  draft: TaskDraft | null;
  /** Правка задачи: её карточка уже загружена экраном 16. */
  taskId?: number;
  /** Дата по умолчанию для новой задачи — выбранный день экрана 15 (route.date, #93). */
  initialDate?: string;
  /**
   * Открыта кнопкой «+ Задача» на экране 15: сохранение новой задачи возвращает туда же,
   * а не на список 14, с выбранным днём — датой созданной задачи (#93, «Решение человека»).
   */
  onCreatedFromMonth?: (dueDate: string) => void;
}

export function TaskFormScreen({
  source,
  today,
  draft,
  taskId,
  initialDate,
  onCreatedFromMonth,
}: Props) {
  const nav = useNavigation();
  const toast = useToast();
  const { cards, upsert } = useCalendarStore();
  const editing = taskId !== undefined ? cards[itemKey("task", taskId)] : undefined;

  const [initial] = useState<Values>(() => {
    if (editing?.type === "task") {
      return {
        title: editing.title,
        date: editing.due_date,
        offset: editing.remind_offset_days,
        time: timeValue(editing.remind_hour, editing.remind_minute),
        done: false,
      };
    }
    // Черновик из чата со временем (например «15:30») — подставляем его и «в день срока»;
    // без времени — как раньше, за 1 день, 10:00 (контракт TIME-BE, #103/#104).
    const draftTime = draft?.remind_hour != null;
    return {
      title: (draft?.title ?? "").slice(0, TITLE_MAX),
      date: draft?.due_date ?? initialDate ?? addDays(today, 1),
      offset: draftTime ? 0 : 1,
      time: draftTime ? timeValue(draft!.remind_hour!, draft?.remind_minute ?? 0) : DEFAULT_TIME,
      done: false,
    };
  });
  const [values, setValues] = useState<Values>(initial);
  const [titleTouched, setTitleTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ErrorKind | null>(null);
  const confirmCancel = useConfirmPress();
  // Ушли с экрана («Назад») до ответа — данные в кэш всё равно, а навигация и тост — нет:
  // иначе повторная навигация увела бы дальше того экрана, куда человек уже вернулся сам
  // (см. SettingsScreen.tsx).
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Правка задачи, которой нет в кэше (не должно случаться: «Перенести» есть только в карточке).
  if (taskId !== undefined && editing?.type !== "task") {
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

  const isNew = taskId === undefined;
  const past = isPast(values.date, today);
  const errors = validate(values, today);
  const canSave = !errors.title && !errors.date && !errors.time && !saving;
  const dirty =
    values.title !== initial.title ||
    values.date !== initial.date ||
    values.offset !== initial.offset ||
    values.time !== initial.time ||
    values.done !== initial.done;
  const set = (patch: Partial<Values>) => setValues((v) => ({ ...v, ...patch }));

  const save = () => {
    if (!canSave) return;
    // У даты в прошлом поле времени скрыто и может быть пустым — тогда шлём время по умолчанию:
    // напоминания всё равно не будет (D32).
    const [hour, minute] = (values.time || DEFAULT_TIME).split(":").map(Number);
    const input: TaskInput = {
      title: values.title.trim(),
      due_date: values.date,
      remind_offset_days: values.offset,
      remind_hour: hour,
      remind_minute: minute,
    };
    // «Уже выполнено» — только новая задача в прошлом; правка отмечается из карточки.
    if (isNew && past && values.done) input.done = true;
    setSaving(true);
    setError(null);
    const request = isNew ? source.createTask(input) : source.updateTask(taskId, input);
    request.then(
      (card) => {
        upsert(card);
        if (!mounted.current) return;
        if (!isNew) {
          nav.back();
          toast(texts.form.updated);
        } else {
          if (onCreatedFromMonth) {
            onCreatedFromMonth(card.due_date);
          } else {
            nav.home();
          }
          toast(texts.form.created);
        }
      },
      (e: unknown) => {
        // Форма не закрывается, введённое остаётся на месте.
        const kind = errorKind(e);
        void source.track("error", { where: "task_form", kind }).catch(() => undefined);
        if (!mounted.current) return;
        setSaving(false);
        setError(kind);
      },
    );
  };

  const cancel = () => {
    if (!dirty || confirmCancel.armed) {
      confirmCancel.reset();
      nav.back();
      return;
    }
    confirmCancel.arm();
  };

  const titleError = titleTouched ? errors.title : null;
  const left = TITLE_MAX - values.title.length;

  return (
    <div className="screen">
      {error && <ErrorBanner kind={error} onRetry={save} retrying={saving} />}
      <Typography.Headline className="form-title">
        {isNew ? texts.form.titleNew : texts.form.titleEdit}
      </Typography.Headline>

      <CellList
        mode="island"
        header={
          <CellHeader
            after={values.title.length > TITLE_COUNTER_FROM && <Counter value={left} rounded />}
          >
            {texts.form.name}
          </CellHeader>
        }
      >
        <CellInput
          aria-label={texts.form.name}
          aria-invalid={titleError ? true : undefined}
          aria-describedby={titleError ? "task-title-error" : undefined}
          placeholder={texts.form.namePlaceholder}
          maxLength={TITLE_MAX}
          value={values.title}
          disabled={saving}
          onChange={(e) => {
            set({ title: e.target.value.slice(0, TITLE_MAX) });
            setTitleTouched(true);
          }}
          onBlur={() => setTitleTouched(true)}
        />
      </CellList>
      {titleError && (
        <Typography.Label id="task-title-error" className="field-error">
          {titleError}
        </Typography.Label>
      )}

      <CellList mode="island" header={<CellHeader>{texts.form.date}</CellHeader>}>
        <div className="form-field">
          <Input
            type="date"
            aria-label={texts.form.date}
            aria-invalid={errors.date ? true : undefined}
            aria-describedby={errors.date ? "task-date-error" : undefined}
            value={values.date}
            disabled={saving}
            onChange={(e) => set({ date: e.target.value })}
          />
        </div>
      </CellList>
      {errors.date && (
        <Typography.Label id="task-date-error" className="field-error">
          {errors.date}
        </Typography.Label>
      )}

      {past ? (
        <>
          {/* Та же подпись, что под полями профиля: отступ и вторичный цвет (app.css). */}
          <Typography.Body className="profile-note">{texts.form.pastNoRemind}</Typography.Body>
          {isNew && (
            <CellList mode="island">
              <CellSimple
                as="label"
                title={texts.form.alreadyDone}
                after={
                  <Switch
                    checked={values.done}
                    aria-disabled={saving || undefined}
                    onChange={(e) => {
                      if (!saving) set({ done: e.target.checked });
                    }}
                  />
                }
              />
            </CellList>
          )}
        </>
      ) : (
        <>
          <CellList
            mode="island"
            role="radiogroup"
            aria-label={texts.form.remind}
            header={<CellHeader>{texts.form.remind}</CellHeader>}
          >
            {REMIND_OFFSETS.map((offset) => (
              <CellSimple
                key={offset}
                as="label"
                before={
                  <Radio
                    name="remind-offset"
                    checked={values.offset === offset}
                    disabled={saving}
                    onChange={() => set({ offset })}
                  />
                }
                title={OFFSET_LABEL[offset]}
              />
            ))}
          </CellList>

          <CellList mode="island" header={<CellHeader>{texts.form.time}</CellHeader>}>
            <div className="form-field">
              <Input
                type="time"
                aria-label={texts.form.time}
                aria-invalid={errors.time ? true : undefined}
                aria-describedby={errors.time ? "task-time-error" : undefined}
                value={values.time}
                disabled={saving}
                onChange={(e) => set({ time: e.target.value })}
              />
            </div>
          </CellList>
          {errors.time && (
            <Typography.Label id="task-time-error" className="field-error">
              {errors.time}
            </Typography.Label>
          )}
        </>
      )}

      <div className="card-actions">
        <Button size="large" stretched loading={saving} disabled={!canSave} onClick={save}>
          {texts.form.save}
        </Button>
        <Button size="large" stretched variant="secondary" disabled={saving} onClick={cancel}>
          {confirmCancel.armed ? texts.form.discardConfirm : texts.form.cancel}
        </Button>
      </div>
    </div>
  );
}
