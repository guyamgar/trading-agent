"""
Strategy library — אסטרטגיות מסחר מסומנות עם חלון זמן, חוקים, וסטטיסטיקה נפרדת.

מבוסס על ניתוח 267 עסקאות סגורות שגילה את 4 השילובים (session × setup × direction)
שמראים WR ≥ 72% ו-EV חיובי משמעותי.

כל אסטרטגיה: שם, חלון UTC, setups+directions מותרים, וכופל גודל פוזיציה.
האנטי-אסטרטגיות הן setups שהדאטה שלנו מראה שהם מפסידים — חוסמים אותם בכל מקרה.
"""
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

ROOT = Path(__file__).parent
STRATEGIES_STATS_FILE = ROOT / "memory" / "strategies_stats.json"


@dataclass
class Strategy:
    """אסטרטגיה אחת — חלון זמן + תנאי setup + ניהול."""
    name: str
    description: str
    # חלון בשעות UTC. אם end_hour < start_hour, החלון עובר חצות.
    start_hour_utc: int
    end_hour_utc: int
    # רשימת setups מותרים. None = כל ה-setups.
    allowed_setups: Optional[List[str]] = None
    # כיוונים מותרים. None = LONG + SHORT.
    allowed_directions: Optional[List[str]] = None
    # multiplier על position size. 1.0 = ברירת מחדל, 1.5 = הגדל ב-50% (אסטרטגיה חזקה)
    position_size_mult: float = 1.0
    # סוג: "blessed" (וודאי-רווחית) או "anti" (לחסום)
    kind: str = "blessed"
    # אם True — האסטרטגיה "מבורכת" רק בשוק דובי מאושש (EMA50<EMA200). אחרת לא נבחרת.
    required_downtrend: bool = False
    # מטא-דאטה לתצוגה
    historical_wr: Optional[float] = None
    historical_trades: Optional[int] = None
    historical_avg_pnl: Optional[float] = None

    def is_active_at(self, dt_utc: datetime) -> bool:
        """האם האסטרטגיה פעילה בשעה ה-UTC הזו?"""
        h = dt_utc.hour
        if self.end_hour_utc > self.start_hour_utc:
            return self.start_hour_utc <= h < self.end_hour_utc
        # חלון שעובר חצות
        return h >= self.start_hour_utc or h < self.end_hour_utc

    def matches_setup(self, setup_type: str, direction: str) -> bool:
        """האם setup+direction תואם לאסטרטגיה הזו?"""
        if self.allowed_setups is not None and setup_type not in self.allowed_setups:
            return False
        if self.allowed_directions is not None and direction not in self.allowed_directions:
            return False
        return True


# ────────────────────────────────────────────────────────────────────────
# הספרייה: 4 אסטרטגיות מבורכות + 1 אנטי
# ────────────────────────────────────────────────────────────────────────

STRATEGY_LIBRARY: List[Strategy] = [
    # ─── אסטרטגיות חדשות מבוססות-מחקר (Tier 1 ממחקר חיצוני) ───
    # סדר חשוב: ספציפיות יותר קודם, כי classify_trade_intent מחזיר ראשון שמתאים.

    Strategy(
        name="London Sweep Reversal",
        description=(
            "Wyckoff Spring + ICT Liquidity Grab בפתיחת לונדון 7-9 UTC. "
            "מחפש נר 15m שעושה wick מתחת לתחתית סשן אסיה (0-7 UTC) אבל סוגר בחזרה בפנים — "
            "stop hunt מהונדס. הסטופים שמתחת ל-Asian Low הוקטפו, ועכשיו נכנסים LONG אחרי "
            "שהוואיל-קונים גמרו לטפל בהם. (Wyckoff 1930s, Pruden \"Three Skills\", "
            "Margex desk writeups, ICT liquidity sweep concept.)"
        ),
        start_hour_utc=7,
        end_hour_utc=9,
        allowed_setups=["Bounce", "Pullback"],  # יכול להיות שניהם — מה שמייחד זה ה-sweep
        allowed_directions=["LONG"],
        position_size_mult=1.4,  # confluence חזק → מגדילים יותר מ-London Bouncer הרגיל
        kind="blessed",
        historical_wr=None,  # אסטרטגיה חדשה - אין סטטיסטיקה אצלנו עדיין
        historical_trades=0,
        historical_avg_pnl=None,
    ),

    Strategy(
        name="NY Opening Range Breakout",
        description=(
            "פריצת הטווח של 15 הדקות הראשונות אחרי פתיחת המניות בארה\"ב (13:30 UTC). "
            "הנר הראשון של 15m אחרי 13:30 קובע טווח. פריצה מעל = LONG, פריצה מתחת = SHORT. "
            "סטופ בצד הנגדי של הטווח, יעד 2R. עובד כי מאז 2024 ETF flow גורם לזרימת "
            "ווליום אמיתי ל-BTC ברגע פתיחת NY. "
            "(Zarattini & Aziz, SSRN 4416622, 2023 — 7000+ מניות, Sharpe חיובי אחרי עלויות.)"
        ),
        start_hour_utc=13,
        end_hour_utc=16,
        allowed_setups=["Breakout"],
        allowed_directions=["LONG", "SHORT"],  # פריצה לכל כיוון
        position_size_mult=1.2,
        kind="blessed",
        historical_wr=None,
        historical_trades=0,
        historical_avg_pnl=None,
    ),

    Strategy(
        name="London Bouncer",
        description="Bounce LONG בפתיחת לונדון 7-13 UTC — תופס דחיות מתחתיות לפני התעוררות נפח. WR היסטורית 82.8%.",
        start_hour_utc=7,
        end_hour_utc=13,
        allowed_setups=["Bounce"],
        allowed_directions=["LONG"],
        position_size_mult=1.3,  # חזק - מגדילים ב-30%
        kind="blessed",
        historical_wr=82.8,
        historical_trades=29,
        historical_avg_pnl=0.67,
    ),
    Strategy(
        name="NY Pullback Trader",
        description="Pullback LONG בסשן ניו יורק 13-21 UTC — רכיבה על מומנטום אמריקאי. WR היסטורית 87.9%.",
        start_hour_utc=13,
        end_hour_utc=21,
        allowed_setups=["Pullback"],
        allowed_directions=["LONG"],
        position_size_mult=1.3,
        kind="blessed",
        historical_wr=87.9,
        historical_trades=33,
        historical_avg_pnl=0.54,
    ),
    Strategy(
        name="Night Owl",
        description="Pullback LONG אחרי סגירת NY 21-24 UTC — נפח נמוך, פחות תחרות, EV גבוה לעסקה.",
        start_hour_utc=21,
        end_hour_utc=24,
        allowed_setups=["Pullback"],
        allowed_directions=["LONG"],
        position_size_mult=1.2,
        kind="blessed",
        historical_wr=81.5,
        historical_trades=27,
        historical_avg_pnl=0.58,
    ),
    Strategy(
        name="Asian Range",
        description="Pullback LONG בסשן אסיה 0-7 UTC — שוק טווחי, רכיבה על תיקונים פנימה.",
        start_hour_utc=0,
        end_hour_utc=7,
        allowed_setups=["Pullback"],
        allowed_directions=["LONG"],
        position_size_mult=1.0,
        kind="blessed",
        historical_wr=77.4,
        historical_trades=31,
        historical_avg_pnl=0.34,
    ),
    # אסטרטגיות-נלוות: NY Pullback SHORT (80% WR, 20 עסקאות, +6.2%) — גם מבורכת
    Strategy(
        name="NY Counter-Trend Short",
        description="Pullback SHORT בסשן NY 13-21 UTC — מנצל אי-נוחות בעליות מוגזמות.",
        start_hour_utc=13,
        end_hour_utc=21,
        allowed_setups=["Pullback"],
        allowed_directions=["SHORT"],
        position_size_mult=1.0,
        kind="blessed",
        historical_wr=80.0,
        historical_trades=20,
        historical_avg_pnl=0.31,
    ),
    # ─── אנטי-אסטרטגיה: לחסום! ───
    Strategy(
        name="ANTI: London Pullback SHORT",
        description="WR 20% בלבד, EV שלילי. חסום בכל מקרה — הוועדה תקבל instruction לדחות.",
        start_hour_utc=7,
        end_hour_utc=13,
        allowed_setups=["Pullback"],
        allowed_directions=["SHORT"],
        position_size_mult=0.0,
        kind="anti",
        historical_wr=20.0,
        historical_trades=5,
        historical_avg_pnl=-0.25,
    ),
    # ─── SHORT מונחה-רג'יים: משלים את ה-regime gate ───
    Strategy(
        name="Bear Short",
        description=(
            "Pullback/Bounce SHORT בשוק דובי מאושש (EMA50<EMA200), בחלון NY+Night+Asia "
            "(מחריג London 7-13 שם short שלילי). משלים את ה-regime gate: כשלונגים חסומים "
            "בירידה — יש מה לסחור. edge דק ומותנה-רג'יים (~+0.1-0.3%/עסקה)."
        ),
        start_hour_utc=13,
        end_hour_utc=7,  # עובר חצות → פעיל 13-23 + 0-6 (NY+Night+Asia)
        allowed_setups=["Pullback", "Bounce"],
        allowed_directions=["SHORT"],
        position_size_mult=1.0,  # edge דק — בלי boost
        kind="blessed",
        required_downtrend=True,
        historical_wr=58.5,
        historical_trades=65,
        historical_avg_pnl=0.119,
    ),
]


# ────────────────────────────────────────────────────────────────────────
# Turn-of-the-Candle Anomaly Booster (Shanaev et al. 2023, SSRN 4192424)
# ────────────────────────────────────────────────────────────────────────
# המאמר מצא: BTC מראה תשואות חיוביות מובהקות (+0.58 bps/min) בדקות 0/15/30/45.
# שאר הדקות נטו שליליות. זה לא אסטרטגיה — זה booster לכל כניסה שקורית
# בחלון תחילת-נר. אנחנו בודקים אם אנחנו ב-3 הדקות הראשונות של נר 15m חדש.

CANDLE_OPEN_MINUTES = [0, 1, 2, 15, 16, 17, 30, 31, 32, 45, 46, 47]
CANDLE_OPEN_SIZE_BOOST = 1.15  # +15% גודל פוזיציה (האפקט קטן אבל מצטבר)


def is_candle_open_minute(dt_utc: Optional[datetime] = None) -> bool:
    """האם אנחנו ב-3 הדקות הראשונות של נר 15m חדש?"""
    if dt_utc is None:
        dt_utc = datetime.utcnow()
    return dt_utc.minute in CANDLE_OPEN_MINUTES


def _is_confirmed_downtrend(market_summary: Optional[Dict]) -> bool:
    """ירידה מאוששת = EMA50 < EMA200 ב-market_summary. אות זהה ל-regime gate; backtest-safe."""
    if not market_summary:
        return False
    ind = market_summary.get("indicators") or {}
    e50, e200 = ind.get("ema_50"), ind.get("ema_200")
    return e50 is not None and e200 is not None and e50 < e200


def find_matching_strategy(timestamp_utc: datetime, setup_type: str, direction: str,
                           market_summary: Optional[Dict] = None) -> Optional[Strategy]:
    """
    מחזיר את האסטרטגיה הראשונה שמתאימה ל-(שעה, setup, direction).
    אסטרטגיה עם required_downtrend נבחרת רק אם market_summary מראה ירידה מאוששת.
    מחזיר None אם לא תואם לאף אחת — סטאפ "ניסיוני".
    """
    for s in STRATEGY_LIBRARY:
        if s.is_active_at(timestamp_utc) and s.matches_setup(setup_type, direction):
            if s.required_downtrend and not _is_confirmed_downtrend(market_summary):
                continue  # אסטרטגיה מותנית-רג'יים, אבל לא בדאון-טרנד → דלג
            return s
    return None


def classify_trade_intent(timestamp_utc: datetime, setup_type: str, direction: str,
                          market_summary: Optional[Dict] = None) -> Dict:
    """
    הפונקציה הראשית שהוועדה/Hunter קוראת לפני החלטה.
    מחזירה: {strategy_name, kind, action_recommended, size_mult, context_for_committee, candle_open_boost}
    """
    s = find_matching_strategy(timestamp_utc, setup_type, direction, market_summary)
    at_candle_open = is_candle_open_minute(timestamp_utc)
    boost_factor = CANDLE_OPEN_SIZE_BOOST if at_candle_open else 1.0
    boost_note = ""
    if at_candle_open:
        boost_note = (
            f"\n🕐 Turn-of-Candle Edge פעיל: דקה {timestamp_utc.minute} (תחילת נר 15m). "
            f"מחקר Shanaev 2023 מראה תשואות חיוביות מובהקות בחלון זה. "
            f"גודל פוזיציה מוכפל ב-{CANDLE_OPEN_SIZE_BOOST} נוסף."
        )

    if s is None:
        base_mult = 0.5  # ניסיוני - חצי גודל
        return {
            "strategy_name": None,
            "kind": "experimental",
            "action_recommended": "trade_small",
            "size_mult": round(base_mult * boost_factor, 3),
            "candle_open_boost": at_candle_open,
            "context_for_committee": (
                f"⚠️ Setup ניסיוני: {setup_type} {direction} בשעה {timestamp_utc.hour} UTC. "
                f"לא תואם לאף אסטרטגיה מבורכת. אם מאשרים — הקטינו פוזיציה."
                + boost_note
            ),
        }

    if s.kind == "anti":
        return {
            "strategy_name": s.name,
            "kind": "anti",
            "action_recommended": "reject",
            "size_mult": 0.0,
            "candle_open_boost": at_candle_open,
            "context_for_committee": (
                f"🚫 חוסם אסטרטגיה: {s.name}. {s.description} "
                f"דחו את העסקה הזו ללא קשר לשאר השיקולים."
            ),
        }

    # blessed
    base_mult = s.position_size_mult
    hist_str = (
        f"WR היסטורית {s.historical_wr}% על {s.historical_trades} עסקאות. "
        if s.historical_trades and s.historical_wr
        else "אסטרטגיה חדשה — עדיין אין סטטיסטיקה אצלנו, מבוססת על מקור חיצוני. "
    )
    return {
        "strategy_name": s.name,
        "kind": "blessed",
        "action_recommended": "trade_normal",
        "size_mult": round(base_mult * boost_factor, 3),
        "candle_open_boost": at_candle_open,
        "context_for_committee": (
            f"✅ אסטרטגיה מבורכת פעילה: {s.name}. {s.description} "
            f"{hist_str}"
            f"במידה ומאשרים — גודל פוזיציה ×{round(base_mult * boost_factor, 3)}."
            + boost_note
        ),
    }


# ────────────────────────────────────────────────────────────────────────
# סטטיסטיקה פר-אסטרטגיה
# ────────────────────────────────────────────────────────────────────────

def _load_stats() -> Dict:
    if not STRATEGIES_STATS_FILE.exists():
        return {}
    try:
        return json.loads(STRATEGIES_STATS_FILE.read_text())
    except Exception:
        return {}


def _save_stats(stats: Dict):
    stats["updated_at"] = datetime.now().isoformat()
    STRATEGIES_STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STRATEGIES_STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, indent=2))


def record_trade_outcome(strategy_name: Optional[str], pnl_pct: float):
    """נקרא אחרי שעסקה נסגרת. מעדכן statistics לאסטרטגיה (או 'experimental')."""
    key = strategy_name or "_experimental_"
    stats = _load_stats()
    s = stats.get(key, {
        "trades": 0, "wins": 0, "losses": 0,
        "total_pnl": 0.0, "best_pnl": -999.0, "worst_pnl": 999.0,
        "first_trade_at": None, "last_trade_at": None,
    })
    s["trades"] += 1
    if pnl_pct > 0:
        s["wins"] += 1
    else:
        s["losses"] += 1
    s["total_pnl"] += pnl_pct
    s["best_pnl"] = max(s["best_pnl"], pnl_pct)
    s["worst_pnl"] = min(s["worst_pnl"], pnl_pct)
    now = datetime.now().isoformat()
    if not s["first_trade_at"]:
        s["first_trade_at"] = now
    s["last_trade_at"] = now
    stats[key] = s
    _save_stats(stats)


def get_strategy_stats() -> Dict:
    """מחזיר את הסטטיסטיקה הנוכחית של כל האסטרטגיות + experimental."""
    return _load_stats()


def get_strategy_by_name(name: str) -> Optional[Strategy]:
    for s in STRATEGY_LIBRARY:
        if s.name == name:
            return s
    return None


def export_library() -> List[Dict]:
    """לוויזואליזציה/דיבוג - מחזיר את כל האסטרטגיות כ-dicts."""
    return [asdict(s) for s in STRATEGY_LIBRARY]


if __name__ == "__main__":
    # smoke test
    print("=== Strategy Library ===")
    for s in STRATEGY_LIBRARY:
        marker = "🚫" if s.kind == "anti" else "✅"
        print(f"{marker} {s.name}: {s.start_hour_utc}-{s.end_hour_utc} UTC, "
              f"setups={s.allowed_setups}, dirs={s.allowed_directions}, "
              f"mult={s.position_size_mult}, WR_hist={s.historical_wr}%")

    # בדיקה: עכשיו, BTCUSDT Pullback LONG
    now = datetime.utcnow()
    intent = classify_trade_intent(now, "Pullback", "LONG")
    print(f"\nעכשיו ({now.hour} UTC), Pullback LONG → {intent}")
    intent = classify_trade_intent(now, "Pullback", "SHORT")
    print(f"\nעכשיו ({now.hour} UTC), Pullback SHORT → {intent}")
