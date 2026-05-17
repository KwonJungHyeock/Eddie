"""
EDDIE 메인 진입점

Phase 0: 환경 셋업 검증용 placeholder
Phase 1: 텍스트 챗 + Tool Use 메인 루프로 확장 예정
"""

from pathlib import Path
import sys


def check_environment() -> bool:
    """Phase 0 환경 셋업이 정상인지 검증합니다."""
    print("=" * 60)
    print("  EDDIE Phase 0 — 환경 셋업 검증")
    print("=" * 60)

    checks = []

    # Python 버전 확인
    py_ok = sys.version_info >= (3, 11)
    checks.append(("Python 3.11+", py_ok, f"현재 {sys.version_info.major}.{sys.version_info.minor}"))

    # 폴더 구조 확인
    project_root = Path(__file__).parent
    required_dirs = ["docs", "src/core", "src/perception", "src/action", "src/output", "src/prompts"]
    for d in required_dirs:
        exists = (project_root / d).is_dir()
        checks.append((f"폴더: {d}", exists, "OK" if exists else "없음"))

    # 핵심 파일 확인
    required_files = [".gitignore", ".env.example", "requirements.txt", "src/prompts/eddie_system_prompt.txt"]
    for f in required_files:
        exists = (project_root / f).is_file()
        checks.append((f"파일: {f}", exists, "OK" if exists else "없음"))

    # 결과 출력
    all_ok = True
    for name, ok, note in checks:
        mark = "[OK]" if ok else "[--]"
        print(f"  {mark}  {name:<40s} {note}")
        if not ok:
            all_ok = False

    print("=" * 60)
    if all_ok:
        print("  Phase 0 환경 셋업 검증 완료")
        print("  → Phase 1 진입 준비됨")
    else:
        print("  일부 항목이 누락되었습니다. 위 [--] 항목을 확인하세요.")
    print("=" * 60)

    return all_ok


def main():
    """EDDIE를 시작합니다."""
    ok = check_environment()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
