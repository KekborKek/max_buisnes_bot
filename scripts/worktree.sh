#!/usr/bin/env bash
# Рабочая копия репозитория для отдельного агента.
#   scripts/worktree.sh new bug-12      создать ../mbb-bug-12 на ветке fix/bug-12 и открыть в VS Code
#   scripts/worktree.sh list            показать все копии
#   scripts/worktree.sh rm bug-12       удалить копию (после слияния PR)
set -euo pipefail

MAIN_DIR="$(git rev-parse --show-toplevel)"
PARENT_DIR="$(dirname "$MAIN_DIR")"
cmd="${1:-}"; name="${2:-}"

case "$cmd" in
  new)
    [ -n "$name" ] || { echo "Укажи имя: scripts/worktree.sh new bug-12"; exit 1; }
    dir="$PARENT_DIR/mbb-$name"
    branch="${3:-fix/$name}"
    git -C "$MAIN_DIR" fetch -q origin main || true
    git -C "$MAIN_DIR" worktree add "$dir" -b "$branch" origin/main
    # .env не хранится в Git — копируем и сдвигаем порты, чтобы копии не конфликтовали
    n=$(git -C "$MAIN_DIR" worktree list | wc -l | tr -d ' ')
    if [ -f "$MAIN_DIR/.env" ]; then
      sed -e "s/^API_PORT=.*/API_PORT=$((8000 + n))/" \
          -e "s/^MINIAPP_PORT=.*/MINIAPP_PORT=$((8080 + n))/" \
          -e "s/^VITE_PORT=.*/VITE_PORT=$((5173 + n))/" "$MAIN_DIR/.env" > "$dir/.env"
    else
      cp "$dir/.env.example" "$dir/.env"
    fi
    (cd "$dir" && make setup)
    echo "Готово: $dir (ветка $branch, API_PORT=$((8000 + n)))"
    command -v code >/dev/null && code "$dir" || echo "Открой папку в VS Code: File → New Window → Open Folder → $dir"
    ;;
  list)
    git -C "$MAIN_DIR" worktree list
    ;;
  rm)
    [ -n "$name" ] || { echo "Укажи имя: scripts/worktree.sh rm bug-12"; exit 1; }
    git -C "$MAIN_DIR" worktree remove "$PARENT_DIR/mbb-$name"
    echo "Копия удалена. Ветку можно удалить: git branch -D fix/$name"
    ;;
  *)
    sed -n '2,5p' "$0"
    ;;
esac
