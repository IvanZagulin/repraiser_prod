#!/bin/bash
# Авто-синхронизация изменений репозитория на GitHub.
# Запускается из cron каждые 10 минут.
# Любые изменения файлов на сервере (кроме .gitignore-исключений) коммитятся
# и пушатся в origin/main. Аутентификация — через ~/.netrc.

REPO="/home/ivanzagulin/repraiser_prod/repraiser_prod"
cd "$REPO" || exit 1

# Нет изменений — выходим тихо
if [ -z "$(git status --porcelain)" ]; then
    exit 0
fi

TS="$(date '+%Y-%m-%d %H:%M:%S')"

# Список изменённых файлов для тела коммита (первые 20)
CHANGED="$(git status --porcelain | head -20)"

git add -A

# commit вернёт ненулевой код если коммитить нечего — тогда просто выходим
if ! git commit -m "auto-sync: $TS" -m "$CHANGED" >/dev/null 2>&1; then
    echo "[$TS] нечего коммитить после add (race?)"
    exit 0
fi

# Пушим; если упало (нет сети/конфликт) — лог и выход без падения cron
if git push origin main >/dev/null 2>&1; then
    echo "[$TS] синхронизировано: $(git rev-parse --short HEAD)"
else
    echo "[$TS] ОШИБКА push — возможно нужен pull/résolve. HEAD=$(git rev-parse --short HEAD)" >&2
    exit 1
fi
