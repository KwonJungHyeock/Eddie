"""
[임시] 검증 스크립트 — 두뇌(LLM) + 도구 격리 테스트.
음성/HUD 없이 OpenAI 호출·도구 사용을 확인한다. 검증 후 삭제 가능.

실행: (venv 활성화 상태에서) python test_llm_real.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src" / "core"))

from dotenv import load_dotenv

load_dotenv()

# 이 스크립트에서만 REAL 모드 강제 (.env 는 건드리지 않음)
os.environ["EDDIE_MOCK_MODE"] = "false"

from reasoning_engine import EddieCore  # noqa: E402

core = EddieCore()
print("=" * 50)
print("모드:", core.get_mode_label())
print("=" * 50)

# 1번: 일반 대화 (도구 X)  /  2번: 검색 필요 (web_search 호출 기대)
questions = [
    "C:\\Users\\RDS\\Documents\\Eddie 폴더에 뭐가 있는지 목록 보여줘",
    "그 안에서 README.md 읽어서 요약해줘",
]

for msg in questions:
    print(f"\n[나] {msg}")
    print(f"[에디] {core.chat(msg)}")

print("\n" + "=" * 50)
print(f"총 턴 수: {core.get_turn_count()}")