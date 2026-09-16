#!/usr/bin/env bash
# Создаёт доску GitHub Projects и раскладывает по ней все задачи репозитория.
#
#   gh auth refresh -s project        # один раз: нужен scope project
#   bash scripts/setup-project-board.sh
#
# Скрипт идемпотентный: повторный запуск не плодит дубли, а дозаполняет
# то, чего не хватает. Это важно — GraphQL API GitHub рвёт соединение
# на длинных сериях запросов, и первый прогон может не дойти до конца.
set -uo pipefail

OWNER="${OWNER:-KekborKek}"
REPO="${REPO:-KekborKek/max_buisnes_bot}"
TITLE="${TITLE:-Парето — каркас и продукт}"

command -v jq >/dev/null || { echo "нужен jq: brew install jq"; exit 1; }
gh project list --owner "$OWNER" >/dev/null 2>&1 || {
  echo "нет доступа к Projects. Выполни: gh auth refresh -s project"; exit 1; }

retry() {  # до 5 попыток с нарастающей паузой
  local n=0
  until "$@"; do
    n=$((n+1)); [ $n -ge 5 ] && { echo "  !! не удалось: $*" >&2; return 1; }
    sleep $((n * 3))
  done
}

# --- доска: переиспользуем существующую с тем же названием ---
NUM=$(gh project list --owner "$OWNER" --format json \
      | jq -r --arg t "$TITLE" '.projects[]|select(.title==$t)|.number' | head -1)
if [ -z "$NUM" ]; then
  NUM=$(gh project create --owner "$OWNER" --title "$TITLE" --format json | jq -r .number)
  echo "доска создана: №$NUM"
else
  echo "доска уже есть: №$NUM"
fi

# --- поля: создаём только недостающие ---
# Имена полей кириллицей — так доску удобнее читать в браузере. Учти: gh портит
# первый байт таких имён в JSON-выводе item-list, поэтому ниже поля всегда
# адресуются по id, а не по имени.
for spec in "Дорожка:LEAD,BOT,API,FRONT,DATA,DOCS" "Приоритет:P0,P1,P2"; do
  name="${spec%%:*}"; opts="${spec#*:}"
  gh project field-list "$NUM" --owner "$OWNER" --format json \
    | jq -e --arg n "$name" '.fields[]|select(.name==$n)' >/dev/null 2>&1 && continue
  retry gh project field-create "$NUM" --owner "$OWNER" --name "$name" \
    --data-type SINGLE_SELECT --single-select-options "$opts" >/dev/null
  echo "поле создано: $name"
done

PID=$(gh project view "$NUM" --owner "$OWNER" --format json | jq -r .id)
F=$(gh project field-list "$NUM" --owner "$OWNER" --format json)
fid(){ jq -r --arg n "$1" '.fields[]|select(.name==$n)|.id' <<<"$F"; }
oid(){ jq -r --arg n "$1" --arg o "$2" \
        '.fields[]|select(.name==$n)|.options[]|select(.name==$o)|.id' <<<"$F"; }
F_LANE=$(fid "Дорожка"); F_PRIO=$(fid "Приоритет"); F_ST=$(fid "Status")

gh issue list --repo "$REPO" --state all --limit 200 \
  --json number,url,state,labels > /tmp/_pb_issues.json

# --- добираем задачи, которых на доске ещё нет ---
ON=$(gh project item-list "$NUM" --owner "$OWNER" --limit 200 --format json \
     | jq -r '.items[].content.number')
jq -r '.[]|"\(.number)\t\(.url)"' /tmp/_pb_issues.json | while IFS=$'\t' read -r n url; do
  grep -qx "$n" <<<"$ON" || { echo "  + #$n"; retry gh project item-add "$NUM" --owner "$OWNER" --url "$url" >/dev/null; }
done

# --- поля. Список читаем заново: только что добавленные появляются с задержкой ---
sleep 2
ITEMS=$(gh project item-list "$NUM" --owner "$OWNER" --limit 200 --format json)
jq -c '.[]' /tmp/_pb_issues.json | while read -r row; do
  n=$(jq -r .number <<<"$row"); state=$(jq -r .state <<<"$row")
  lane=$(jq -r '[.labels[].name]|map(select(startswith("lane:")))|.[0]//""|sub("lane:";"")' <<<"$row")
  prio=$(jq -r '[.labels[].name]|map(select(test("^P[012]$")))|.[0]//""' <<<"$row")
  item=$(jq -r --argjson n "$n" '.items[]|select(.content.number==$n)|.id' <<<"$ITEMS")
  [ -n "$item" ] || { echo "  ?? #$n не найдена на доске — перезапусти скрипт"; continue; }
  st=$([ "$state" = CLOSED ] && echo Done || echo Todo)
  for pair in "$F_LANE|Дорожка|$lane" "$F_PRIO|Приоритет|$prio" "$F_ST|Status|$st"; do
    IFS='|' read -r f fname val <<<"$pair"
    [ -n "$val" ] || continue
    o=$(oid "$fname" "$val"); [ -n "$o" ] || continue
    retry gh project item-edit --id "$item" --project-id "$PID" \
      --field-id "$f" --single-select-option-id "$o" >/dev/null
  done
  printf "  #%-3s %-6s %-3s %s\n" "$n" "${lane:--}" "${prio:--}" "$st"
done

echo
echo "Готово: https://github.com/users/$OWNER/projects/$NUM"
