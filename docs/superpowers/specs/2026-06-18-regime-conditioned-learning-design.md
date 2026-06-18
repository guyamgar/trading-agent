# Spec: Regime-Conditioned Learning — זיכרון מודע-רג'יים

**תאריך:** 2026-06-18
**מצב:** Design (ממתין לאישור משתמש לפני writing-plans)
**הקשר:** תת-פרויקט C מתוך 3 שדרוגי self-learning (A Reality-Check ✅ → **C** → B Explorer). spec→plan→build נפרד.

---

## 1. רקע ובעיה

הלקחים (`lessons.json`, 283 כיום) **לא מתויגים ברג'יים** — שדותיהם: `rule, trigger, category, from_outcome, confidence, times_invoked, times_wrong`. גרוע מכך: `relevant_lessons(market_summary, ...)` **מתעלם לחלוטין מ-`market_summary`** ומחזיר את N הלקחים האחרונים. כלומר לקח שנלמד ב-bull מוזרק ישירות להחלטה ב-bear, ולהיפך — הזיכרון של הממשלה עיוור-רג'יים. (זה גם תורם לבעיית ה-overfit שראינו.)

## 2. מטרות ולא-מטרות

**מטרות:**
1. **טקסונומיית רג'יים דטרמיניסטית** משותפת: `classify_regime(market_summary) -> "bull"|"bear"|"range"` (מ-יישור EMA + EMA50/200, עקבי עם ה-gate/Reality-Check).
2. **לתייג כל לקח חדש** ב-`regime` ברגע היצירה.
3. **`relevant_lessons` מודע-רג'יים — תעדוף:** לקחים מהרג'יים הנוכחי קודם, מילוי בשאר; לקחים ישנים ללא תג נחשבים "מילוי" (תאימות לאחור).
4. **סטטיסטיקות-אסטרטגיה לפי רג'יים:** `record_trade_outcome` מקבל `regime`, שומר תת-סטטיסטיקה per-regime (מזין גם את Reality-Check ו-Explorer).

**לא-מטרות:**
- לא משנה את ה-coach/critic prompts (רק מוסיף תיוג למבנה הנשמר).
- לא Explorer / גילוי setups (תת-פרויקט B).
- לא משנה איך ה-Reality-Check פועל (רק מספק לו דאטה עשיר יותר; הוא ימשיך לעבוד כמו שהוא).

## 3. עיצוב

### 3.1 `classify_regime` (דטרמיניסטי, ב-`strategies.py`)
```
bull  = EMA9>EMA21>EMA50  AND EMA50>EMA200
bear  = EMA9<EMA21<EMA50  AND EMA50<EMA200
range = אחרת
```
קורא רק מ-`market_summary["indicators"]` (ema_9/21/50/200) ו/או `market_summary["trend"]`. backtest-safe, בלי רשת. עקבי עם `_is_confirmed_downtrend` (bear ⊇ אותו תנאי EMA50<EMA200).

### 3.2 תיוג לקחים ביצירה
בנקודת יצירת הלקח (אחרי עסקה — ה-coach/`run_critique` → `save_lesson`), מוסיפים `regime = classify_regime(<market context of the trade>)`. מקור ההקשר: ה-`market_summary` של העסקה אם קיים ברשומה; אחרת מיפוי `מצב_מקרו` של קורא-ההקשר (Bull→bull, Bear→bear, Range/Transition→range); אחרת `"unknown"`. לקחים קיימים (283) נשארים ללא תג → נחשבים "unknown".

### 3.3 `relevant_lessons` מודע-רג'יים (תעדוף)
```
cur = classify_regime(market_summary)
matched = [l for l in lessons if l.get("regime") == cur]            # תואמי-רג'יים
fill    = [l for l in lessons if l not in matched]                   # השאר (כולל unknown/ישנים)
return (matched[-limit:] + fill[-(limit-len(matched_taken)):])[-limit:]  # matched קודם, מילוי בשאר
```
שומר על הפרמטר `min_confidence` הקיים. אם אין תואמי-רג'יים (רג'יים קר) — מתנהג כמו היום (מילוי באחרונים). תאימות לאחור מלאה.

### 3.4 סטטיסטיקות-אסטרטגיה לפי רג'יים
`record_trade_outcome(strategy_name, pnl_pct, regime=None)` — בנוסף ל-bucket הקיים, שומר תת-מבנה `by_regime[regime]` עם אותם שדות (trades/wins/losses/total_pnl). הקוראים (live_monitor, נתיב סגירת paper) מעבירים את ה-regime (מ-classify_regime של ה-market_summary של העסקה). מבנה קיים נשמר (תאימות לאחור).

## 4. בדיקות

1. **`classify_regime`:** EMA עולה-מסודר+cross → "bull"; יורד-מסודר+cross → "bear"; מעורב → "range"; indicators חסרים → "range" (ברירת מחדל בטוחה).
2. **תיוג לקח:** לקח שנוצר בהקשר bear → `regime=="bear"`; ללא market context → "unknown".
3. **`relevant_lessons` תעדוף:** עם לקחים מתויגים bull+bear ו-market_summary של bear → מחזיר את ה-bear קודם; כשאין תואמים → מתנהג כמו היום (אחרונים); לקחים ישנים ללא תג עדיין ניתנים לבחירה כמילוי.
4. **`record_trade_outcome` per-regime:** קריאה עם regime="bear" → `by_regime["bear"]` מתעדכן + ה-bucket הראשי נשמר.
5. assert-scripts (אין pytest), על דאטה מסונתז.

## 5. שער הצלחה

- רגרסיה: `relevant_lessons` על הדאטה הקיים לא קורס (לקחים ישנים ללא תג → מילוי), ועם market_summary של רג'יים מסוים מתעדף נכון.
- לקחים חדשים שייווצרו (בבוט החי) ייכתבו עם `regime`.

## 6. סיכונים

- **רוב הלקחים (283) ללא תג** → בהתחלה ה-תעדוף כמעט לא משנה (כולם "unknown"=מילוי). זה תקין — ההשפעה צומחת ככל שנצברים לקחים מתויגים. אפשר (אופציונלי, נציין בלבד) backfill חד-פעמי של regime ללקחים ישנים מ-trades — אבל זה דורש klines היסטוריים (רשת) ולכן **מחוץ ל-scope** ל-v1.
- **תאימות לאחור:** כל השינויים אדיטיביים (שדה `regime` אופציונלי, param אופציונלי) — קוד קיים לא נשבר.
- branch נפרד עד review/merge, כמו הקודמים.
