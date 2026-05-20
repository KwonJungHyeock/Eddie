"""
EDDIE 메인 진입점

Phase 1 — 텍스트 챗 + Tool Use MVP
Step 1-2 (현재): MOCK 모드 콘솔 챗 루프
"""

import sys
from pathlib import Path

# Windows cmd에서 UTF-8 한글 입출력 안전 처리
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

# 프로젝트 루트를 import path에 추가
sys.path.insert(0, str(Path(__file__).parent))

# .env 파일 로드 (python-dotenv)
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

# EDDIE 코어 로드
from src.core.reasoning_engine import EddieCore  # noqa: E402


# === 콘솔 UI 유틸 ===

LINE = "=" * 60


def print_banner(eddie: EddieCore) -> None:
    print(LINE)
    print("  EDDIE — Eduino's Digital Development Intelligent Engineer")
    print(LINE)
    print(f"  모드      : {eddie.get_mode_label()}")
    print(f"  사용자    : {eddie.user_title}")
    print(f"  시스템    : Phase 1 / Step 1-2 (MOCK 콘솔 챗 루프)")
    print(LINE)
    print("  명령: /help  /mode  /clear  /exit")
    print(LINE)
    print()


def print_help() -> None:
    print()
    print("  명령 도움말:")
    print("    /help    이 도움말 표시")
    print("    /mode    현재 모드 / 대화 턴 수 확인")
    print("    /clear   대화 히스토리 초기화")
    print("    /exit    EDDIE 종료 (또는 /quit, Ctrl+C)")
    print()


# === 메인 루프 ===


def main() -> int:
    """EDDIE 콘솔 챗 루프."""
    # EDDIE 초기화
    try:
        eddie = EddieCore()
    except Exception as exc:
        print(f"  [에러] EDDIE 초기화 실패: {exc}")
        return 1

    print_banner(eddie)

    # 챗 루프
    while True:
        try:
            user_input = input(f"{eddie.user_title} > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n  종료합니다.")
            break

        if not user_input:
            continue

        # 특수 명령 처리
        if user_input in ("/exit", "/quit"):
            print("  종료합니다.")
            break

        if user_input == "/help":
            print_help()
            continue

        if user_input == "/mode":
            print(f"  현재 모드 : {eddie.get_mode_label()}")
            print(f"  대화 턴   : {eddie.get_turn_count()}회")
            continue

        if user_input == "/clear":
            eddie.clear_history()
            print("  대화 히스토리를 초기화했습니다.")
            continue

        # EDDIE 응답
        response = eddie.chat(user_input)
        print(f"EDDIE > {response}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
