"""
[임시] 1단계 검증 스크립트 — 두뇌(LLM)만 격리 테스트.
음성/HUD 없이 OpenAI 호출·한국어 응답만 확인한다.
검증 후 삭제해도 됨.

실행: (venv 활성화 상태에서) python test_llm_real.py
"""

import os
import sys
from pathlib import Path

# src/core 를 import 경로에 추가
sys.path.insert(0, str(Path(__file__).parent / "src" / "core"))

from dotenv import load_dotenv

load_dotenv()  # .env 의 OPENAI_API_KEY 등 로드

# 이 스크립트에서만 REAL 모드 강제 (.env 는 건드리지 않음)
os.environ["EDDIE_MOCK_MODE"] = "false"

from reasoning_engine import EddieCore  # noqa: E402

core = EddieCore()
print("=" * 50)
print("모드:", core.get_mode_label())
print("=" * 50)

for msg in ["에디야 안녕", "너는 누구야?", "지금 무슨 작업 중이야?"]:
    print(f"\n[나] {msg}")
    print(f"[에디] {core.chat(msg)}")

print("\n" + "=" * 50)
print(f"총 턴 수: {core.get_turn_count()}")
