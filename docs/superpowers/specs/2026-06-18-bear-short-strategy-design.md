# Spec: Bear Short — אסטרטגיית SHORT מונחית-רג'יים

**תאריך:** 2026-06-18
**מצב:** Design (ממתין לאישור משתמש לפני writing-plans)
**קשור:** משלים את `2026-06-18-regime-gate-design.md`. כשה-gate חוסם LONG בירידה, זה נותן למערכת SHORT מבורך לסחור באותו רג'יים.

---

## 1. רקע ובעיה

המערכת long-biased (5/7 אסטרטגיות LONG). ה-regime gate (R1) חוסם LONG כשהשוק יורד (EMA50<EMA200) — אבל אז אין לה מה לסחור, כי כיסוי ה-SHORT דל. ניתוח הדאטה (86 עסקאות SHORT):

| חתך | n | WR | EV/עסקה |
|---|---|---|---|
| כל SHORT | 86 | 54.7% | +0.084% |
| **SHORT ב-Bear regime** | 65 | **58.5%** | **+0.119%** |
| Pullback SHORT (כל סשן) | 35 | 68.6% | +0.205% |
| Pullback SHORT ב-NY (קיים: NY Counter-Trend Short) | 21 | 81.0% | +0.312% |
| SHORT ב-Range/Transition/Bull | ~11 | שלילי | <0 |
| Pullback SHORT ב-London (קיים: ANTI) | 5 | 20.0% | −0.251% |

**המסקנה:** ה-edge של SHORT אמיתי אך **דק ומותנה-רג'יים** — עובד בשוק דובי, נכשל ב-Range/Bull. התנאי הוא **regime, לא session**. (זה גם ההפך ממלכודת ה-overfit שכבר נפלנו בה — לא מברכים תאי session קטנים.)

## 2. מטרות ולא-מטרות

**מטרות:**
1. להפוך Pullback/Bounce SHORT ל**מבורך כשהרג'יים דובי-מאושש** (אותו אות EMA50<EMA200 של ה-gate), כך שיש מה לסחור כשה-gate חוסם לונגים.
2. לשמור SHORT כ-experimental/חסום ב-Range/Transition/Bull (שם ה-EV שלילי).
3. להרחיב את `classify_trade_intent` להיות regime-aware (תוספת לשימוש חוזר), בלי look-ahead.
4. לאמת רב-תקופתית לפני ברכה מלאה — שלא נחזור על ה-overfit.

**לא-מטרות:**
- מחקר setups דוביים חדשים (breakdown/distribution) — נדחה (Option B שלא נבחר).
- Kelly / שינוי גודל פוזיציה — מוקפא (spec אחר).
- $27K reset — מוקפא עד שמוכח edge עמיד-רג'יים מצטבר (gate + short).

## 3. עיצוב

### 3.1 אסטרטגיה חדשה: "Bear Short"
ב-`strategies.py` `STRATEGY_LIBRARY`:
```
Strategy(
  name="Bear Short",
  start_hour_utc=13, end_hour_utc=7,   # NY+Night+Asia (עובר חצות); מחריג London 7-13 (שם short שלילי)
  allowed_setups=["Pullback", "Bounce"],
  allowed_directions=["SHORT"],
  position_size_mult=1.0,              # edge דק — בלי boost
  kind="blessed",
  required_downtrend=True,             # שדה חדש (ראה 3.2)
)
```
- **סדר ברשימה:** אחרי ה-ANTI הקיים ("London Pullback SHORT", 7-13) ואחרי "NY Counter-Trend Short" (13-21 Pullback SHORT) — כך ש-London-short פוגע ב-anti, NY-pullback-short נשאר אצל האסטרטגיה הקיימת המוכחת, ו-Bear Short תופס את Asia/Night + Bounce-short.

### 3.2 הפיכת `classify_trade_intent` ל-regime-aware (backtest-safe)
- הוספת שדה `required_downtrend: bool = False` ל-`Strategy`.
- `find_matching_strategy(timestamp, setup, direction, market_summary=None)` — כשמועמד בעל `required_downtrend=True`, מדלגים עליו אם `market_summary` לא מראה ירידה מאוששת (`indicators.ema_50 < indicators.ema_200`). כך הוא "מבורך" רק בדאון-טרנד; אחרת ההתאמה נופלת ל-experimental.
- `classify_trade_intent(timestamp, setup, direction, market_summary=None)` — מעבירה את `market_summary` הלאה. ללא `market_summary` (תאימות לאחור) — אסטרטגיות `required_downtrend` לא נבחרות (ברירת מחדל בטוחה: לא מברכים בלי הוכחת רג'יים).
- **אות הרג'יים זהה ל-gate** (EMA50<EMA200 מ-`market_summary`) — מקור אמת אחד, backtest-safe, בלי look-ahead.

### 3.3 חיבור ב-`run_committee`
`agents/orchestrator.py` (~שורה 115) כבר קורא `classify_trade_intent(utcnow, setup..., direction)` ויש לו `market_summary` בהישג יד — מעבירים אותו: `classify_trade_intent(..., market_summary=market_summary)`. שאר הזרימה (blessed→size_mult, experimental→חצי, anti→reject) ללא שינוי.

### 3.4 אינטראקציה עם ה-gate
ה-gate (R1) חוסם SHORT רק בעלייה מאוששת (EMA50>EMA200). Bear Short מברך SHORT רק בירידה מאוששת (EMA50<EMA200). באזור הביניים (range, EMA50≈EMA200) — ה-gate מתיר, ו-Bear Short לא מברך → ה-setup נשאר experimental (חצי גודל / נחסם ע"י Section A כשתופעל). עקבי עם הדאטה (range-short ≈ אפס).

## 4. בדיקות

1. **יחידה — `find_matching_strategy`/`classify_trade_intent`:**
   - Pullback SHORT ב-NY/Asia/Night + downtrend (ema50<ema200) → blessed "Bear Short" (או "NY Counter-Trend Short" ל-NY).
   - אותו setup ללא downtrend (ema50>ema200) → לא blessed (experimental).
   - Pullback SHORT ב-London + downtrend → עדיין anti (הסדר שומר על קדימות ה-anti).
   - `market_summary=None` → Bear Short לא נבחר.
2. **רגרסיה על הדאטה האמיתי** (`scripts/validate_bear_short.py`): על כל עסקאות ה-SHORT הסגורות, לסווג מחדש עם הלוגיקה החדשה ולהראות: ה-blessed-Bear-Short שמסומנות הן חיוביות (EV>0), וה-shorts ב-Range/Transition/Bull לא מסומנות blessed.

## 5. שער הצלחה / ולידציה

- (קוד) רגרסיה: blessed Bear Short על הדאטה הקיים = EV חיובי; shorts לא-דוביים לא מבורכים.
- (אופרטיבי, נדחה לפני אמון מלא) **בקטסט רב-תקופתי:** להריץ על ≥2-3 תקופות דוביות שונות מ-Binance ולהראות EV נטו חיובי בכל אחת בנפרד — שלא נברך overfit.
- **כֵּנוּת:** edge דק (~+0.1-0.3%/עסקה). מוסיף EV ותדירות בירידות, לא מביא לבד ל-$1,200/חודש.

## 6. סיכונים

- **Overfit חוזר:** מוקטן ע"י תנאי-רג'יים (מדגם גדול) במקום תאי-session, + ולידציה רב-תקופתית לפני אמון.
- **תדירות נמוכה:** אם דאון-טרנדים נדירים, Bear Short יורה מעט — מקובל; הוא רשת ביטחון לרג'יים, לא מנוע ראשי.
- **תלות ב-market_summary:** אם לא מועבר, האסטרטגיה לא נבחרת (בטוח, לא קורס).
- **לא git merge:** ממשיכים על branch `regime-gate` (או branch חדש) — להחליט בסיום.
