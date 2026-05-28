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
        """연결된 보드 목록을 조회한다 (포트·이름·FQBN).

        배제 포트(기본 COM1: 시스템 내장 시리얼)는 제외한다.
        EDDIE_ARDUINO_EXCLUDE_PORTS(쉼표구분)로 추가 배제 가능.
        인식된 보드(FQBN 있음)를 목록 앞쪽으로 정렬한다.
        """
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

        # 배제 포트 집합 (기본 COM1 + env 추가분), 대소문자 무시
        exclude = {"com1"}
        env_excl = os.getenv("EDDIE_ARDUINO_EXCLUDE_PORTS", "")
        for p in env_excl.split(","):
            p = p.strip().lower()
            if p:
                exclude.add(p)

        boards = []
        for entry in entries:
            port = (entry.get("port") or {}).get("address") or entry.get("address")
            if not port or port.strip().lower() in exclude:
                continue  # COM1 등 배제 포트 스킵
            matching = entry.get("matching_boards") or entry.get("boards") or []
            if matching:
                name = matching[0].get("name", "Unknown")
                fqbn = matching[0].get("fqbn", "")
            else:
                name, fqbn = "Unknown", ""
            boards.append({"port": port, "name": name, "fqbn": fqbn})

        # 인식된 보드(FQBN 있음)를 앞으로
        boards.sort(key=lambda b: (b["fqbn"] == "",))

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

    @staticmethod
    def _parse_serial_values(lines: list[str]) -> dict:
        """시리얼 줄들에서 수치를 뽑아 시리즈로 정리.

        지원: 'Temp: 24.5' / 'Humidity=45' / 'T:24.5 H:45' / CSV '24.5,45' / 단일 '24.5'
        반환: {label: [values...], ...}
        """
        series: dict[str, list[float]] = {}

        def add(label: str, val: str) -> None:
            try:
                series.setdefault(label, []).append(float(val))
            except ValueError:
                pass

        for ln in lines:
            ln = (ln or "").strip()
            if not ln:
                continue
            pairs = re.findall(r"([A-Za-z가-힣_]+)\s*[:=]\s*(-?\d+\.?\d*)", ln)
            if pairs:
                for label, val in pairs:
                    add(label.strip(), val)
                continue
            nums = re.findall(r"-?\d+\.?\d*", ln)
            if len(nums) >= 2:
                for i, v in enumerate(nums):
                    add(f"ch{i+1}", v)
            elif len(nums) == 1:
                add("value", nums[0])
        return series

    def _read_serial_raw(
        self, port: Optional[str], baud: int, duration_s: float, max_lines: int
    ) -> dict:
        """시리얼 원시 줄 읽기 (read_serial/show_*가 공유)."""
        try:
            import serial  # pyserial
        except ImportError:
            return {"status": "error", "message": "pyserial 미설치. 'pip install pyserial' 필요."}

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
                end = _time.time() + max(0.5, min(duration_s, 30.0))
                while _time.time() < end and len(lines) < max_lines:
                    raw = ser.readline()
                    if not raw:
                        continue
                    text = raw.decode("utf-8", errors="replace").strip()
                    if text:
                        lines.append(text)
        except Exception as e:  # noqa: BLE001
            return {
                "status": "error",
                "port": resolved_port,
                "message": f"시리얼 읽기 실패: {type(e).__name__}: {e}. "
                           f"(포트 점유 여부·baud 확인)",
            }
        return {"status": "ok", "port": resolved_port, "baud": baud, "lines": lines}

    def show_value(
        self, port: Optional[str] = None, baud: int = 9600, duration_s: float = 3.0
    ) -> dict:
        """시리얼에서 최신 수치를 읽어 HUD '수치 패널'용 데이터를 만든다.

        반환 dict의 'hud' 키를 호출부(EddieCore)가 DataBus로 HUD에 전달한다.
        """
        raw = self._read_serial_raw(port, baud, duration_s, max_lines=60)
        if raw["status"] != "ok":
            return raw
        series = self._parse_serial_values(raw["lines"])
        if not series:
            return {"status": "error", "message": "수치를 찾지 못했습니다. baud·출력 형식 확인."}
        # 각 라벨의 최신값
        latest = {label: vals[-1] for label, vals in series.items() if vals}
        return {
            "status": "ok",
            "port": raw["port"],
            "values": latest,
            "hud": {"kind": "value", "values": latest},
        }

    def show_plot(
        self, port: Optional[str] = None, baud: int = 9600, duration_s: float = 6.0
    ) -> dict:
        """시리얼 값을 일정시간 모아 HUD '시리얼 플로터'용 시계열을 만든다."""
        raw = self._read_serial_raw(port, baud, duration_s, max_lines=300)
        if raw["status"] != "ok":
            return raw
        series = self._parse_serial_values(raw["lines"])
        if not series:
            return {"status": "error", "message": "그릴 수치를 찾지 못했습니다. baud·출력 형식 확인."}
        return {
            "status": "ok",
            "port": raw["port"],
            "points": {k: len(v) for k, v in series.items()},
            "hud": {"kind": "plot", "series": series},
        }

    # ====================================================================
    # 실시간 모니터링 (S2) — 시리얼을 계속 읽어 콜백으로 최신값 스트리밍
    # ====================================================================
    def start_monitor(
        self,
        on_update,
        port: Optional[str] = None,
        baud: int = 9600,
        window: int = 60,
        interval_s: float = 0.4,
    ) -> dict:
        """시리얼 상시 모니터링 시작.

        백그라운드 스레드에서 시리얼을 계속 읽으며, interval_s마다
        on_update(payload)를 호출한다. payload는 다음 두 종류를 함께 담는다:
          - 최신값(values): 수치 패널용
          - 시계열(series): 그래프용 (최근 window개 슬라이딩)
        '값/그래프 보여줘' 시 시작, '닫아줘/그만' 시 stop_monitor로 중지.

        on_update: callable(dict) — WebSocket broadcast 등으로 연결.
        """
        try:
            import serial  # noqa: F401  (가용성 확인)
        except ImportError:
            return {"status": "error", "message": "pyserial 미설치. 'pip install pyserial' 필요."}

        # 이미 돌고 있으면 재시작
        self.stop_monitor()

        resolved_port = port or self.default_port
        if not resolved_port:
            info = self.list_boards()
            if info.get("status") == "ok" and info.get("boards"):
                resolved_port = info["boards"][0]["port"]
        if not resolved_port:
            return {"status": "error", "message": "연결된 보드를 찾지 못했습니다."}

        import threading
        from collections import deque, OrderedDict

        self._monitor_stop = threading.Event()
        self._monitor_port = resolved_port

        def _worker():
            import time as _t
            import serial as _serial
            # 라벨별 슬라이딩 윈도우
            history: "OrderedDict[str, deque]" = OrderedDict()
            latest: dict = {}
            last_emit = 0.0
            try:
                ser = _serial.Serial(resolved_port, baud, timeout=0.3)
            except Exception as e:  # noqa: BLE001
                on_update({"kind": "monitor_error",
                           "message": f"시리얼 열기 실패: {type(e).__name__}: {e}"})
                return
            try:
                while not self._monitor_stop.is_set():
                    raw = ser.readline()
                    if raw:
                        text = raw.decode("utf-8", errors="replace").strip()
                        if text:
                            parsed = self._parse_serial_values([text])
                            for label, vals in parsed.items():
                                if not vals:
                                    continue
                                v = vals[-1]
                                latest[label] = v
                                if label not in history:
                                    history[label] = deque(maxlen=window)
                                history[label].append(v)
                    # 주기적으로만 emit (과도한 push 방지)
                    now = _t.monotonic()
                    if now - last_emit >= interval_s and latest:
                        last_emit = now
                        on_update({
                            "kind": "monitor",
                            "values": dict(latest),
                            "series": {k: list(v) for k, v in history.items()},
                        })
            except Exception as e:  # noqa: BLE001
                on_update({"kind": "monitor_error",
                           "message": f"모니터링 중단: {type(e).__name__}: {e}"})
            finally:
                try:
                    ser.close()
                except Exception:  # noqa: BLE001
                    pass

        self._monitor_thread = threading.Thread(target=_worker, daemon=True)
        self._monitor_thread.start()
        return {"status": "ok", "port": resolved_port, "message": "실시간 모니터링을 시작했습니다."}

    def stop_monitor(self) -> dict:
        """모니터링 중지."""
        ev = getattr(self, "_monitor_stop", None)
        th = getattr(self, "_monitor_thread", None)
        if ev is not None:
            ev.set()
        if th is not None and th.is_alive():
            th.join(timeout=1.5)
        self._monitor_thread = None
        return {"status": "ok", "message": "모니터링을 중지했습니다."}

    def is_monitoring(self) -> bool:
        th = getattr(self, "_monitor_thread", None)
        return th is not None and th.is_alive()
