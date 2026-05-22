"""
MOD-TOL-005 — 아두이노 제어 도구 (Arduino Control)

기술 기반: arduino-cli (subprocess 래핑)
의도: 사용자 음성 명령에 따라 LLM이 작성한 스케치를 컴파일·업로드.
      컴파일 에러는 그대로 반환하여 LLM이 스스로 수정 후 재컴파일하게 한다.
입력: 스케치 이름, 코드, (옵션) 포트·FQBN
출력: dict (status, 메시지, 컴파일 에러 등)
핵심 책임: 보드 감지, 스케치 저장, 컴파일(에러 캡처), 업로드

설정 (.env, 모두 선택):
  EDDIE_ARDUINO_CLI         : arduino-cli 실행 경로 (기본 "arduino-cli", PATH 등록 시 그대로)
  EDDIE_ARDUINO_FQBN        : 기본 보드 FQBN (기본 "arduino:avr:uno" = Uno R3)
  EDDIE_ARDUINO_PORT        : 기본 포트 (미지정 시 board list로 자동 감지)
  EDDIE_ARDUINO_SKETCH_DIR  : 스케치 저장 폴더 (기본 <프로젝트>/arduino_sketches)

Phase 1 확장 — MOD-TOL-005.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional


class ArduinoControlError(Exception):
    """아두이노 제어 검증/실행 실패."""


class ArduinoControl:
    """arduino-cli 기반 아두이노 보드 제어 도구."""

    DEFAULT_TIMEOUT = 180  # 컴파일/업로드 여유 (초)

    # 헤더(#include)·별칭 → arduino-cli 라이브러리 정확한 이름 매핑.
    # 에듀이노 주력 센서 우선 등록. 없는 건 lib search 로 폴백.
    LIBRARY_MAP = {
        # DHT 온습도 (의존성: Adafruit Unified Sensor)
        "dht.h": "DHT sensor library",
        "dht": "DHT sensor library",
        "dht11": "DHT sensor library",
        # NeoPixel
        "adafruit_neopixel.h": "Adafruit NeoPixel",
        "neopixel": "Adafruit NeoPixel",
        # LCD1602 I2C
        "liquidcrystal_i2c.h": "LiquidCrystal I2C",
        "lcd1602": "LiquidCrystal I2C",
        "lcd": "LiquidCrystal I2C",
        # HC-SR04 초음파 (라이브러리 없이 pulseIn 가능하나, 쓸 경우 NewPing)
        "newping.h": "NewPing",
        "sr04": "NewPing",
        "hc-sr04": "NewPing",
    }
    # 일부 라이브러리는 의존성 동반 설치 필요
    LIBRARY_DEPS = {
        "DHT sensor library": ["Adafruit Unified Sensor"],
    }

    def __init__(
        self,
        cli_path: Optional[str] = None,
        default_fqbn: Optional[str] = None,
        default_port: Optional[str] = None,
        sketch_dir: Optional[str] = None,
    ) -> None:
        self.cli_path = cli_path or os.getenv("EDDIE_ARDUINO_CLI", "arduino-cli")
        self.default_fqbn = default_fqbn or os.getenv(
            "EDDIE_ARDUINO_FQBN", "arduino:avr:uno"
        )
        self.default_port = default_port or os.getenv("EDDIE_ARDUINO_PORT") or None

        if sketch_dir:
            base = Path(sketch_dir)
        elif os.getenv("EDDIE_ARDUINO_SKETCH_DIR"):
            base = Path(os.getenv("EDDIE_ARDUINO_SKETCH_DIR"))
        else:
            # <프로젝트>/arduino_sketches  (이 파일: <root>/src/action/arduino_control.py)
            base = Path(__file__).resolve().parent.parent.parent / "arduino_sketches"
        self.sketch_dir = base

    # === 내부 유틸 ===

    def _run(self, args: list[str], timeout: int | None = None) -> tuple[int, str, str]:
        """arduino-cli 실행. (returncode, stdout, stderr) 반환."""
        try:
            proc = subprocess.run(
                [self.cli_path, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or self.DEFAULT_TIMEOUT,
            )
            return proc.returncode, proc.stdout or "", proc.stderr or ""
        except FileNotFoundError as e:
            raise ArduinoControlError(
                f"arduino-cli 를 찾을 수 없습니다 ({self.cli_path}). "
                f"PATH 등록 또는 EDDIE_ARDUINO_CLI 설정이 필요합니다."
            ) from e
        except subprocess.TimeoutExpired as e:
            raise ArduinoControlError(f"명령 시간 초과: {' '.join(args)}") from e

    @staticmethod
    def _safe_name(name: str) -> str:
        """스케치 이름 정규화 (영숫자·밑줄만, arduino-cli 규칙)."""
        name = (name or "").strip()
        name = re.sub(r"[^0-9A-Za-z_]", "_", name)
        name = name.strip("_") or "sketch"
        return name

    def _sketch_path(self, name: str) -> Path:
        """스케치 폴더 경로 (sketch_dir/<name>)."""
        return self.sketch_dir / self._safe_name(name)

    # === 공개 메서드 ===

    def list_boards(self) -> dict:
        """연결된 보드 목록을 조회한다 (포트·이름·FQBN)."""
        try:
            rc, out, err = self._run(["board", "list", "--format", "json"], timeout=30)
        except ArduinoControlError as e:
            return {"status": "error", "message": str(e)}

        try:
            data = json.loads(out or "{}")
        except json.JSONDecodeError:
            return {"status": "error", "message": f"board list 파싱 실패: {err or out}"}

        # 1.x: {"detected_ports": [...]}, 구버전: [...] 둘 다 처리
        if isinstance(data, dict):
            entries = data.get("detected_ports", [])
        elif isinstance(data, list):
            entries = data
        else:
            entries = []

        boards = []
        for entry in entries:
            port = (entry.get("port") or {}).get("address") or entry.get("address")
            matching = entry.get("matching_boards") or entry.get("boards") or []
            if matching:
                name = matching[0].get("name", "Unknown")
                fqbn = matching[0].get("fqbn", "")
            else:
                name, fqbn = "Unknown", ""
            if port:
                boards.append({"port": port, "name": name, "fqbn": fqbn})

        return {"status": "ok", "count": len(boards), "boards": boards}

    def _resolve_port_fqbn(
        self, port: Optional[str], fqbn: Optional[str], need_port: bool
    ) -> tuple[Optional[str], str]:
        """포트·FQBN 결정. 미지정 시 board list 자동 감지 → env 기본값 순."""
        resolved_fqbn = fqbn or self.default_fqbn
        resolved_port = port or self.default_port

        if need_port and not resolved_port:
            # 자동 감지: FQBN 있는 첫 보드의 포트
            info = self.list_boards()
            if info.get("status") == "ok":
                for b in info.get("boards", []):
                    if b.get("fqbn"):
                        resolved_port = b["port"]
                        if not fqbn:
                            resolved_fqbn = b["fqbn"]
                        break
                # FQBN 매칭이 없으면 포트라도 첫 번째
                if not resolved_port and info.get("boards"):
                    resolved_port = info["boards"][0]["port"]

        if need_port and not resolved_port:
            raise ArduinoControlError(
                "연결된 보드를 찾지 못했습니다. USB 연결을 확인하거나 "
                "EDDIE_ARDUINO_PORT 를 설정해 주십시오."
            )
        return resolved_port, resolved_fqbn

    def write_sketch(self, name: str, code: str) -> dict:
        """스케치 코드를 저장한다 (sketch_dir/<name>/<name>.ino)."""
        safe = self._safe_name(name)
        sketch_path = self._sketch_path(safe)
        try:
            sketch_path.mkdir(parents=True, exist_ok=True)
            ino = sketch_path / f"{safe}.ino"
            ino.write_text(code or "", encoding="utf-8")
        except OSError as e:
            return {"status": "error", "message": f"스케치 저장 실패: {e}"}
        return {
            "status": "ok",
            "sketch": safe,
            "path": str(ino),
            "message": "스케치 저장 완료",
        }

    def compile(self, name: str, fqbn: Optional[str] = None) -> dict:
        """스케치를 컴파일한다. 실패 시 컴파일러 에러를 그대로 반환."""
        safe = self._safe_name(name)
        sketch_path = self._sketch_path(safe)
        if not sketch_path.exists():
            return {"status": "error", "message": f"스케치 없음: {safe}. 먼저 작성하십시오."}

        _, resolved_fqbn = self._resolve_port_fqbn(None, fqbn, need_port=False)
        try:
            rc, out, err = self._run(
                ["compile", "--fqbn", resolved_fqbn, str(sketch_path)]
            )
        except ArduinoControlError as e:
            return {"status": "error", "message": str(e)}

        if rc == 0:
            return {
                "status": "ok",
                "sketch": safe,
                "fqbn": resolved_fqbn,
                "message": "컴파일 성공",
            }
        # 컴파일 실패 → LLM이 읽고 수정하도록 에러 텍스트 반환
        error_text = (err or out or "").strip()
        if len(error_text) > 3000:
            error_text = error_text[:3000] + "\n...(생략)"
        return {
            "status": "error",
            "sketch": safe,
            "fqbn": resolved_fqbn,
            "message": "컴파일 실패",
            "compiler_error": error_text,
        }

    def upload(
        self, name: str, port: Optional[str] = None, fqbn: Optional[str] = None
    ) -> dict:
        """컴파일된 스케치를 보드에 업로드한다."""
        safe = self._safe_name(name)
        sketch_path = self._sketch_path(safe)
        if not sketch_path.exists():
            return {"status": "error", "message": f"스케치 없음: {safe}. 먼저 작성하십시오."}

        try:
            resolved_port, resolved_fqbn = self._resolve_port_fqbn(
                port, fqbn, need_port=True
            )
            rc, out, err = self._run(
                ["upload", "-p", resolved_port, "--fqbn", resolved_fqbn, str(sketch_path)]
            )
        except ArduinoControlError as e:
            return {"status": "error", "message": str(e)}

        if rc == 0:
            return {
                "status": "ok",
                "sketch": safe,
                "port": resolved_port,
                "fqbn": resolved_fqbn,
                "message": "업로드 완료",
            }
        error_text = (err or out or "").strip()
        if len(error_text) > 2000:
            error_text = error_text[:2000] + "\n...(생략)"
        return {
            "status": "error",
            "sketch": safe,
            "port": resolved_port,
            "message": "업로드 실패",
            "error": error_text,
        }

    def install_library(self, name: str) -> dict:
        """라이브러리를 설치한다.

        name: 헤더명(예: 'DHT.h'), 별칭(예: 'neopixel'), 또는 정확한 라이브러리명.
              매핑 테이블에 있으면 정확한 이름으로 변환, 없으면 입력값 그대로 시도.
        의존성이 있는 라이브러리는 함께 설치한다.
        """
        raw = (name or "").strip()
        if not raw:
            return {"status": "error", "message": "라이브러리 이름이 비어 있습니다."}

        # 매핑: 헤더/별칭 → 정확한 이름 (소문자 키)
        resolved = self.LIBRARY_MAP.get(raw.lower(), raw)

        # 설치 대상 = 의존성 먼저, 그다음 본체
        targets = list(self.LIBRARY_DEPS.get(resolved, [])) + [resolved]
        installed, failed = [], []
        for lib in targets:
            try:
                rc, out, err = self._run(["lib", "install", lib], timeout=120)
            except ArduinoControlError as e:
                return {"status": "error", "message": str(e)}
            if rc == 0:
                installed.append(lib)
            else:
                failed.append({"lib": lib, "error": (err or out or "").strip()[:300]})

        if failed:
            return {
                "status": "error",
                "message": "일부 라이브러리 설치 실패",
                "requested": raw,
                "resolved": resolved,
                "installed": installed,
                "failed": failed,
            }
        return {
            "status": "ok",
            "requested": raw,
            "resolved": resolved,
            "installed": installed,
            "message": f"라이브러리 설치 완료: {', '.join(installed)}",
        }

    def search_library(self, query: str) -> dict:
        """라이브러리를 검색한다 (정확한 이름을 모를 때)."""
        q = (query or "").strip()
        if not q:
            return {"status": "error", "message": "검색어가 비어 있습니다."}
        try:
            rc, out, err = self._run(["lib", "search", q, "--format", "json"], timeout=60)
        except ArduinoControlError as e:
            return {"status": "error", "message": str(e)}
        try:
            data = json.loads(out or "{}")
        except json.JSONDecodeError:
            return {"status": "error", "message": f"검색 결과 파싱 실패: {(err or out)[:200]}"}
        libs = data.get("libraries", data if isinstance(data, list) else [])
        names = []
        for lib in libs[:10]:
            nm = lib.get("name") if isinstance(lib, dict) else None
            if nm:
                names.append(nm)
        return {"status": "ok", "query": q, "count": len(names), "results": names}

    def read_serial(
        self,
        port: Optional[str] = None,
        baud: int = 9600,
        duration_s: float = 3.0,
        max_lines: int = 30,
    ) -> dict:
        """업로드 후 보드의 시리얼 출력을 읽는다 (센서값 확인·디버깅용).

        duration_s 동안 시리얼을 수신해 줄 단위로 모은다. pyserial 사용.
        """
        try:
            import serial  # pyserial
        except ImportError:
            return {
                "status": "error",
                "message": "pyserial 미설치. 'pip install pyserial' 필요.",
            }

        resolved_port = port or self.default_port
        if not resolved_port:
            info = self.list_boards()
            if info.get("status") == "ok" and info.get("boards"):
                resolved_port = info["boards"][0]["port"]
        if not resolved_port:
            return {"status": "error", "message": "연결된 보드를 찾지 못했습니다."}

        import time as _time
        lines: list[str] = []
        try:
            with serial.Serial(resolved_port, baud, timeout=0.5) as ser:
                end = _time.time() + max(0.5, min(duration_s, 15.0))
                while _time.time() < end and len(lines) < max_lines:
                    raw = ser.readline()
                    if not raw:
                        continue
                    text = raw.decode("utf-8", errors="replace").strip()
                    if text:
                        lines.append(text)
        except Exception as e:  # noqa: BLE001 (serial.SerialException 등)
            return {
                "status": "error",
                "port": resolved_port,
                "message": f"시리얼 읽기 실패: {type(e).__name__}: {e}. "
                           f"(포트 점유 여부·baud 확인)",
            }

        return {
            "status": "ok",
            "port": resolved_port,
            "baud": baud,
            "line_count": len(lines),
            "lines": lines,
        }
