"""
state_bus.py — EDDIE 상태 버스 (프로세스 간 상태 공유)

목적: 음성 백엔드(Python)와 GUI(Electron) 간 상태 동기화.

설계 원칙 (ARC-002 To-Be 대비):
  - StateBus 추상 인터페이스로 통신 방식을 추상화
  - 현재(Phase 3): FileStateBus — JSON 파일 기반 (단순, 의존성 0)
  - 미래(Phase 6, 피지컬 컴퓨팅): WebSocketStateBus 로 교체 예정
    → 인터페이스 불변, 구현체만 교체하면 voice_chat 등 호출부는 안 바뀜

상태 종류 (DSN-001 4상태):
  idle / listening / thinking / speaking
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


# 유효 상태 (DSN-001 정의)
VALID_STATES = ("idle", "listening", "thinking", "speaking")


class StateBus(ABC):
    """상태 버스 추상 인터페이스.

    구현체: FileStateBus (현재), WebSocketStateBus (미래).
    호출부(voice_chat 등)는 이 인터페이스에만 의존한다.
    """

    @abstractmethod
    def set_state(self, state: str, detail: Optional[dict] = None) -> None:
        """현재 상태를 발행한다.
        state: idle/listening/thinking/speaking
        detail: 선택적 부가 정보 (예: {"text": "검색 중"})
        """
        ...

    @abstractmethod
    def get_state(self) -> dict:
        """현재 상태를 읽는다. 반환: {"state": str, "detail": dict, "ts": float}"""
        ...

    def reset(self) -> None:
        """idle 로 초기화 (기본 구현)."""
        self.set_state("idle")


class FileStateBus(StateBus):
    """JSON 파일 기반 상태 버스 (Phase 3 구현).

    Python 이 파일에 쓰고, HUD(Electron)가 주기적으로 읽는다.
    단순·안정적이며 외부 의존성이 없다.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        """
        path: 상태 파일 경로. None 이면 프로젝트 루트의 eddie_state.json.
              Electron(main.js)도 같은 경로를 읽어야 하므로 프로젝트 루트 고정.
              (OS 임시폴더는 백신/환경마다 위치가 달라 불일치 발생 → 프로젝트 루트 사용)
        """
        if path is None:
            # 프로젝트 루트 = 이 파일(src/core/state_bus.py)의 2단계 상위
            project_root = Path(__file__).resolve().parents[2]
            path = str(project_root / "eddie_state.json")
        self.path = Path(path)
        # 초기 상태 기록
        try:
            if not self.path.exists():
                self._write({"state": "idle", "detail": {}, "ts": time.time()})
        except OSError:
            pass

    def set_state(self, state: str, detail: Optional[dict] = None) -> None:
        if state not in VALID_STATES:
            raise ValueError(
                f"유효하지 않은 상태: {state}. 가능: {VALID_STATES}"
            )
        payload = {
            "state": state,
            "detail": detail or {},
            "ts": time.time(),
        }
        self._write(payload)

    def get_state(self) -> dict:
        try:
            text = self.path.read_text(encoding="utf-8")
            return json.loads(text)
        except (OSError, json.JSONDecodeError):
            return {"state": "idle", "detail": {}, "ts": 0.0}

    def _write(self, payload: dict) -> None:
        """원자적 쓰기 (임시 파일 → rename) 로 읽기 충돌 방지."""
        try:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(str(tmp), str(self.path))
        except OSError:
            # 쓰기 실패는 치명적이지 않음 (GUI 동기화만 영향)
            pass

    def get_path(self) -> str:
        """HUD 가 읽을 파일 경로 반환 (Electron 설정용)."""
        return str(self.path)


# ===========================================================================
# 데모 / 자체 검증
# ===========================================================================

def _demo():
    import sys
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    print("=" * 60)
    print("  StateBus 데모 (FileStateBus)")
    print("=" * 60)

    bus = FileStateBus()
    print(f"  상태 파일 경로: {bus.get_path()}")
    print(f"  (Electron HUD 가 이 파일을 읽습니다)")
    print()

    # 4상태 순환 시연
    for state in VALID_STATES:
        bus.set_state(state, detail={"note": f"{state} 테스트"})
        read = bus.get_state()
        print(f"  set: {state:12s} → get: {read['state']:12s} detail={read['detail']}")
        assert read["state"] == state, "상태 불일치!"

    # 유효성 검사
    print()
    try:
        bus.set_state("invalid_state")
        print("  [실패] 잘못된 상태가 통과됨")
    except ValueError as e:
        print(f"  [정상] 잘못된 상태 거부: {e}")

    bus.reset()
    print(f"  reset 후: {bus.get_state()['state']}")
    print("=" * 60)
    print("  데모 완료")
    print("=" * 60)


if __name__ == "__main__":
    _demo()
