"""
command_bus.py — EDDIE 명령 버스 (GUI → Python 방향)

목적: HUD(Electron)에서 발생한 사용자 명령(녹음 시작 등)을
      음성 백엔드(Python)로 전달.

설계: StateBus 와 짝을 이루는 반대 방향 채널.
  - StateBus     : Python → HUD  (상태 발행)
  - CommandBus   : HUD → Python  (명령 발행)  ← 이 파일

추상화 (ARC-002 대비):
  - 현재: FileCommandBus (JSON 파일 + 카운터로 중복 방지)
  - 미래: WebSocketCommandBus 로 교체 가능 (인터페이스 불변)

명령 종류:
  record  : 녹음 토글 (시작/종료)
  stop    : 종료 요청
  (확장 가능)
"""

from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


VALID_COMMANDS = ("record", "stop", "none")


class CommandBus(ABC):
    """명령 버스 추상 인터페이스 (HUD → Python)."""

    @abstractmethod
    def send_command(self, command: str, detail: Optional[dict] = None) -> None:
        """명령을 발행한다 (HUD 측에서 호출)."""
        ...

    @abstractmethod
    def poll_command(self) -> Optional[dict]:
        """새 명령이 있으면 반환, 없으면 None (Python 측에서 호출).
        한 번 읽은 명령은 다시 반환하지 않는다 (seq 카운터로 중복 방지)."""
        ...


class FileCommandBus(CommandBus):
    """JSON 파일 기반 명령 버스.

    HUD(Electron preload)가 파일에 명령+seq 를 쓰고,
    Python(voice_chat)이 폴링하며 새 seq 만 처리한다.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        if path is None:
            project_root = Path(__file__).resolve().parents[2]
            path = str(project_root / "eddie_command.json")
        self.path = Path(path)
        self._last_seq = -1  # Python 측이 마지막으로 처리한 seq
        # 초기화
        try:
            if not self.path.exists():
                self._write({"command": "none", "seq": 0, "detail": {}, "ts": time.time()})
        except OSError:
            pass

    def send_command(self, command: str, detail: Optional[dict] = None) -> None:
        """HUD 측: 명령 발행. seq 를 증가시켜 새 명령임을 표시."""
        if command not in VALID_COMMANDS:
            raise ValueError(f"유효하지 않은 명령: {command}. 가능: {VALID_COMMANDS}")
        # 기존 seq 읽어서 +1
        current = self._read()
        seq = int(current.get("seq", 0)) + 1
        self._write({
            "command": command,
            "seq": seq,
            "detail": detail or {},
            "ts": time.time(),
        })

    def poll_command(self) -> Optional[dict]:
        """Python 측: 새 명령(아직 처리 안 한 seq)이 있으면 반환."""
        data = self._read()
        seq = int(data.get("seq", 0))
        if seq > self._last_seq:
            self._last_seq = seq
            cmd = data.get("command", "none")
            if cmd != "none":
                return data
        return None

    def sync_seq(self) -> None:
        """Python 시작 시 현재 seq 에 동기화 (과거 명령 무시)."""
        data = self._read()
        self._last_seq = int(data.get("seq", 0))

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"command": "none", "seq": 0, "detail": {}, "ts": 0.0}

    def _write(self, payload: dict) -> None:
        try:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(str(tmp), str(self.path))
        except OSError:
            pass

    def get_path(self) -> str:
        return str(self.path)


# ===========================================================================
# 자체 검증
# ===========================================================================

def _demo():
    import sys
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    print("=" * 60)
    print("  CommandBus 데모 (FileCommandBus)")
    print("=" * 60)

    bus = FileCommandBus(path="/tmp/eddie_cmd_test.json")
    bus.sync_seq()
    print(f"  파일: {bus.get_path()}")

    # 명령 없을 때
    print(f"  초기 poll: {bus.poll_command()}  (None 이어야 함)")

    # HUD가 record 발행
    bus.send_command("record", detail={"source": "spacebar"})
    got = bus.poll_command()
    print(f"  record 발행 후 poll: {got}")
    assert got and got["command"] == "record", "명령 수신 실패"

    # 같은 명령 재폴링 — 중복 안 나와야
    print(f"  재폴링 (중복 방지): {bus.poll_command()}  (None 이어야 함)")

    # 연속 명령
    bus.send_command("record")
    bus.send_command("stop")
    print(f"  마지막 명령만: {bus.poll_command()}")  # stop (최신)

    print("=" * 60)
    print("  데모 완료")
    print("=" * 60)


if __name__ == "__main__":
    _demo()
