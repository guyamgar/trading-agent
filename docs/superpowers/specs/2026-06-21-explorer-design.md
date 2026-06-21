# Spec: Explorer — גילוי אסטרטגיות אוטונומי (data-mining v1)

**תאריך:** 2026-06-21
**מצב:** Design (המשתמש אישר אוטונומיה מלאה — בלי gating; ראה [[feedback-autonomous-execution]])
**הקשר:** תת-פרויקט B (האחרון) מתוך שדרוגי self-learning. A (Reality-Check) ו-C (regime-learning) כבר חיים.

---

## 1. רקע ובעיה

הראינו שה-edge דק ונדיר → ל-$1,200/חודש צריך **setups/אסטרטגיות חדשות**, לא עוד כיוונון. ה-Tuner הקיים מכוונן (פרמטרים/לקחים/פרומפטים) אבל **לא מגלה אסטרטגיות חדשות**. 4 המבורכות המקוריות ו-Bear Short נמצאו ע"י ניתוח-דאטה ידני של צירופי session×setup×direction. ה-Explorer **מְמַכֵּן** את הגילוי הזה, מותנה-רג'יים, ומקדם אוטומטית — נשמר ע"י ה-Reality-Check.

## 2. מטרות ולא-מטרות

**מטרות:**
1. רכיב דטרמיניסטי `explorer.py` שכורה את העסקאות הסגורות ומוצא צירופי **(session × setup × direction × regime)** לא-מבורכים עם edge חזק ומדגם מספיק.
2. **קידום אוטומטי בגודל קטן:** מועמד מאומת נכנס כאסטרטגיה "discovered" בגודל מוקטן (size_mult 0.5), וה-**Reality-Check** מגדיל/מנמיך אותה לפי ביצועי-לייב (מנצל את `live_size_mult` הקיים).
3. ספים מחמירים נגד overfit (דאטה דק כרגע).
4. התראת טלגרם על כל גילוי.

**לא-מטרות:**
- לא LLM-generative (המצאת סוגי-setup חדשים) — זה v2.
- לא נוגע ב-Tuner / Hunter.
- לא מקדם בגודל מלא (האוטונומיה היא "קטן + RC שופט").

## 3. עיצוב

### 3.1 `explorer.py` — גילוי (דטרמיניסטי)
- `discover_candidates(trades) -> List[Dict]`: מקבץ עסקאות סגורות לפי (session-bucket, setup, direction, regime). regime מ-`classify_regime` של ה-market context של העסקה (או "unknown" — מדלגים על unknown). לכל תא: n, WR, EV.
- **סף קידום מחמיר (אנטי-overfit):** `n ≥ 15` **וגם** `WR ≥ 70%` **וגם** `EV ≥ +0.25%`.
- **אי-כפילות:** מדלגים על תאים שכבר מכוסים ע"י אסטרטגיה קיימת (`STRATEGY_LIBRARY`) או discovered קיימת (אותו setup+direction בחלון חופף).
- session-bucket → שעות: Asia=0-7, London=7-13, NY=13-21, Night=21-24.

### 3.2 קידום ל-`discovered_strategies.json`
`promote_candidate(cand)` כותב רשומה: `{name, start_hour_utc, end_hour_utc, allowed_setups, allowed_directions, required_regime, position_size_mult: 0.5, kind:"blessed", source:"explorer", discovered_at, hist_wr, hist_trades}`. שם ייחודי, למשל `"Explorer: NY Bounce LONG (bear)"`.

### 3.3 שילוב ב-`classify_trade_intent`/`find_matching_strategy`
`find_matching_strategy` יבדוק גם את ה-discovered strategies (אחרי ה-`STRATEGY_LIBRARY` ההמקודד — קדימות נמוכה יותר). מועמד discovered תואם אם `is_active_at` + setup + direction **וגם** `classify_regime(market_summary) == required_regime`. כש-`market_summary` חסר — discovered לא נבחר (בטוח). זה blessed בגודל 0.5; ה-`live_size_mult` של ה-Reality-Check מכפיל אותו → גדל אם מכליל בלייב, מתכווץ אם לא. **כך מתממש "auto-קטן + RC שופט".**

### 3.4 `run_explorer()` + לולאה
`run_explorer()`: discover → promote חדשים (שלא קיימים) → מחזיר summary. לולאה ב-bot.py **שבועית** (גילוי משתנה לאט) שמריצה ומתריעה בטלגרם על מועמדים שקודמו. מכבד `is_paused()`.

## 4. בדיקות

1. `discover_candidates`: trades מסונתזים עם תא חזק (n≥15, WR≥70%, EV≥0.25%) שלא מכוסה → מוחזר; תא מתחת לסף → לא; תא שכבר ב-STRATEGY_LIBRARY → לא (אי-כפילות); regime="unknown" → מדולג.
2. `promote_candidate`: כותב ל-discovered_strategies.json עם size 0.5 + required_regime + source.
3. שילוב: discovered strategy תואם ב-`classify_trade_intent` רק כשהרג'יים תואם; כש-market_summary חסר — לא נבחר; ה-size מוכפל ב-live_size_mult של RC.
4. `run_explorer`: לא מקדם כפילויות; מחזיר summary; (אם אין מועמדים — ריק, בלי קריסה).
5. assert-scripts, דאטה מסונתז, בלי רשת/LLM.

## 5. שער הצלחה

- רגרסיה על 285 העסקאות: `run_explorer()` רץ בלי קריסה; עם הספים המחמירים — סביר שלא ימצא כלום עדיין (דאטה דק), וזה תקין (לא ממציא מ-overfit). מתעורר ככל שנצבר forward-data.
- discovered strategy שמקודמת אכן נבחרת ב-classify ומושפעת מ-RC.

## 6. סיכונים

- **דאטה דק → overfit:** ממותן ע"י ספים מחמירים (n≥15) + גודל-קטן + RC שופט + regime-conditioning. ה-Explorer יציע נדיר ובביטחון.
- **כפילות עם הקיים:** בדיקת אי-כפילות מול STRATEGY_LIBRARY + discovered.
- **אינטראקציה עם gate/RC/regime:** discovered הוא blessed → ה-gate עדיין חוסם LONG בדאון-טרנד; ה-required_regime מצמצם; ה-live_size_mult של RC מכפיל. הכל מצטבר, לא מתנגש.
- branch נפרד; merge + restart בסוף.
