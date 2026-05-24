"""
מריץ 3 סשני למידה ברצף.
כל סשן = 3 כניסות מאושרות. סה"כ 9 עסקאות פוטנציאליות.
מחכה לסיום כל סשן לפני שמתחיל את הבא.
"""
import sys
import subprocess
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
LEARN_SCRIPT = ROOT / "scripts" / "learn_daily.py"


def run_session(num: int) -> int:
    print(f"\n{'#' * 70}")
    print(f"# סשן {num}/3 - מתחיל ב-{datetime.now().strftime('%H:%M:%S')}")
    print(f"{'#' * 70}\n")

    result = subprocess.run(
        [sys.executable, "-u", str(LEARN_SCRIPT)],
        cwd=str(ROOT),
    )
    return result.returncode


def main():
    print(f"מריץ 3 סשנים ברצף החל מ-{datetime.now().strftime('%H:%M:%S')}\n")

    for i in range(1, 4):
        rc = run_session(i)
        if rc != 0:
            print(f"\n⚠️  סשן {i} נסתיים עם exit code {rc}")
        else:
            print(f"\n✅ סשן {i} הסתיים בהצלחה")

    print(f"\n{'=' * 70}")
    print(f"כל 3 הסשנים הושלמו ב-{datetime.now().strftime('%H:%M:%S')}")
    print(f"{'=' * 70}")
    print("\nלצפייה בכל הלקחים: cat memory/lessons.json | python3 -m json.tool")
    print("לסטטיסטיקה: python3 -c 'from memory_store import get_stats; print(get_stats())'")


if __name__ == "__main__":
    main()
