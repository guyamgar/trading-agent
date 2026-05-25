#!/bin/bash
# גיבוי יומי של הזיכרון ל-GitHub
# רץ אוטומטית מהבוט ב-23:30 (אחרי התקציר היומי)

set -e
cd "$(dirname "$0")/.."

# רק אם יש שינויים בזיכרון
if git diff --quiet memory/ 2>/dev/null && git diff --cached --quiet memory/ 2>/dev/null; then
    # אם אין שינויים גם ב-tracked - יוצאים
    if [ -z "$(git status --porcelain memory/)" ]; then
        echo "[$(date +%H:%M)] אין שינויים בזיכרון - דילוג"
        exit 0
    fi
fi

git add memory/ logs/ 2>/dev/null || true
git add -A

# לקטוף נתונים לתאריך commit
TRADES=$(python3 -c "import json; d=json.load(open('memory/trades.json')); t = d if isinstance(d,list) else d.get('trades',[]); print(len(t))" 2>/dev/null || echo "?")
LESSONS=$(python3 -c "import json; d=json.load(open('memory/lessons.json')); l = d if isinstance(d,list) else d.get('lessons',[]); print(len(l))" 2>/dev/null || echo "?")
BALANCE=$(python3 -c "import json; print(json.load(open('memory/account.json'))['current_balance'])" 2>/dev/null || echo "?")

COMMIT_MSG="Auto-backup $(date +%Y-%m-%d) - ${TRADES} trades, ${LESSONS} lessons, balance \$${BALANCE}"

git commit -m "$COMMIT_MSG" 2>&1 | tail -3
git push origin main 2>&1 | tail -2
echo "[$(date +%H:%M)] גיבוי הושלם"
