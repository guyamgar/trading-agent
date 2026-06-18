import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import memory_store as M

BULL = {"trend":"?","indicators":{"ema_9":110,"ema_21":108,"ema_50":105,"ema_200":100}}
BEAR = {"trend":"?","indicators":{"ema_9":90,"ema_21":92,"ema_50":95,"ema_200":100}}

def _reset():
    M.LESSONS_FILE.write_text(json.dumps({"lessons": []}, ensure_ascii=False))

def test_save_lesson_tags_regime_from_market_summary():
    _reset()
    lid = M.save_lesson({"rule":"r","trigger":"t","market_summary": BEAR})
    saved = [l for l in M.load_lessons() if l["id"]==lid][0]
    assert saved["regime"] == "bear", saved
    assert "market_summary" not in saved  # popped, not persisted

def test_save_lesson_defaults_unknown():
    _reset()
    lid = M.save_lesson({"rule":"r","trigger":"t"})
    saved = [l for l in M.load_lessons() if l["id"]==lid][0]
    assert saved["regime"] == "unknown", saved

def test_relevant_lessons_prioritizes_current_regime():
    _reset()
    M.save_lesson({"rule":"bull-lesson","trigger":"t","regime":"bull"})
    M.save_lesson({"rule":"bear-lesson","trigger":"t","regime":"bear"})
    M.save_lesson({"rule":"old-untagged","trigger":"t"})  # regime=unknown
    picked = M.relevant_lessons(BEAR, limit=1)
    assert len(picked) == 1 and picked[0]["rule"] == "bear-lesson", picked

def test_relevant_lessons_fills_when_no_match():
    _reset()
    M.save_lesson({"rule":"a","trigger":"t","regime":"bull"})
    M.save_lesson({"rule":"b","trigger":"t","regime":"bull"})
    picked = M.relevant_lessons(BEAR, limit=2)  # no bear lessons → fall back to recent
    assert len(picked) == 2, picked

if __name__ == "__main__":
    fns = [v for k,v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns: fn(); print(f"PASS {fn.__name__}")
    print(f"\nALL {len(fns)} TESTS PASSED")
