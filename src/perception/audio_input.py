"""
MOD-IN-001 — 음성 입력 캡처 (Phase 2 Step 2-4)

기술 기반: sounddevice (PortAudio 바인딩)
의도: 마이크에서 실시간 PCM 오디오 캡처. Whisper STT 입력 형식
      (16kHz, mono, float32)에 맞게 정규화.
입력: 마이크 신호 (analog)
출력: numpy.ndarray (16kHz, mono, float32, -1.0~+1.0)
핵심 책임: 오디오 캡처, 녹음 제어 (Enter 종료 PTT), 디바이스 관리, 입력 검증

발화 종료 감지 방식: PTT (Push-to-Talk)
  - record_until_enter(): Enter 입력까지 무한 녹음 (가장 안정적)
  - record_fixed(): 고정 시간 녹음 (테스트용)
  - VAD (자동 침묵 감지)는 Phase 6 이후 검토.
"""

from __future__ import annotations

import sys
import threading
import wave
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd


class MicrophoneError(Exception):
    """마이크 캡처 실패."""


class MicrophoneCapture:
    """sounddevice 기반 마이크 캡처.

    Whisper 표준 입력 형식 (16kHz, mono, float32) 준수.
    PTT 방식 (Enter 키로 종료) 채택 — Phase 2 검증용 안정성 최우선.
    """

    SAMPLE_RATE = 16000
    CHANNELS = 1
    DTYPE = np.float32

    def __init__(self, device: Optional[int] = None, auto_select: bool = True) -> None:
        """
        device: None + auto_select=True 이면 16kHz 마이크 자동 선택.
                정수면 sd.query_devices() 결과의 인덱스 직접 지정.
        """
        if device is None and auto_select:
            device = self.find_best_device()
        self.device = device

    # === 디바이스 정보 ===

    @staticmethod
    def list_devices() -> list[dict]:
        """입력 가능한 마이크 디바이스 목록 반환."""
        try:
            devices = sd.query_devices()
        except Exception as e:
            raise MicrophoneError(f"디바이스 조회 실패: {e}") from e

        inputs = []
        for i, dev in enumerate(devices):
            if dev["max_input_channels"] > 0:
                inputs.append({
                    "index": i,
                    "name": dev["name"],
                    "channels": dev["max_input_channels"],
                    "default_samplerate": int(dev["default_samplerate"]),
                })
        return inputs

    @staticmethod
    def default_input_device() -> dict:
        """현재 OS 기본 마이크 정보."""
        try:
            default_input = sd.default.device[0]
            if default_input == -1 or default_input is None:
                return {"index": None, "name": "(기본 디바이스 미지정)", "available": False}
            info = sd.query_devices(default_input)
            return {
                "index": default_input,
                "name": info["name"],
                "channels": info["max_input_channels"],
                "available": True,
            }
        except Exception as e:
            return {"index": None, "name": f"감지 실패: {e}", "available": False}

    @staticmethod
    def find_best_device() -> Optional[int]:
        """16kHz 우선, 그다음 입력 채널 있는 첫 디바이스를 자동 선택.
        WEBCAM/USB 마이크의 16kHz 모드를 우선 (Whisper 표준 매칭).
        반환: device index 또는 None (기본 디바이스 사용)."""
        try:
            devices = sd.query_devices()
        except Exception:
            return None

        candidates_16k = []
        candidates_other = []
        for i, dev in enumerate(devices):
            if dev["max_input_channels"] <= 0:
                continue
            sr = int(dev.get("default_samplerate", 0))
            name = dev.get("name", "")
            # Microsoft 사운드 매퍼/주 드라이버는 MME라 에러 잦음 → 스킵 우선
            is_mapper = "사운드 매퍼" in name or "사운드 캡처" in name or "Sound Mapper" in name
            if sr == 16000 and not is_mapper:
                candidates_16k.append(i)
            elif not is_mapper:
                candidates_other.append(i)

        if candidates_16k:
            return candidates_16k[0]
        if candidates_other:
            return candidates_other[0]
        return None

    # === 녹음 ===

    def record_until_enter(self, prompt: str = "녹음 중... Enter로 종료: ") -> np.ndarray:
        """Enter 키 누를 때까지 녹음. 반환: float32 numpy array (16kHz mono)."""
        chunks: list[np.ndarray] = []
        stop_event = threading.Event()

        def callback(indata, frames, time_info, status):
            # status 에 underrun/overflow 등 입력 문제가 들어옴. 일단 침묵.
            if not stop_event.is_set():
                chunks.append(indata.copy())

        try:
            stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=self.CHANNELS,
                dtype=self.DTYPE,
                callback=callback,
                device=self.device,
                blocksize=1024,
            )
        except Exception as e:
            raise MicrophoneError(f"마이크 스트림 초기화 실패: {e}") from e

        try:
            stream.start()
        except Exception as e:
            stream.close()
            raise MicrophoneError(f"마이크 스트림 시작 실패: {e}") from e

        try:
            input(prompt)
        finally:
            stop_event.set()
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

        if not chunks:
            raise MicrophoneError("녹음된 데이터 없음 (마이크 입력 없음?)")

        audio = np.concatenate(chunks, axis=0).flatten().astype(self.DTYPE)
        return audio

    def record_fixed(self, duration_seconds: float = 5.0) -> np.ndarray:
        """고정 시간 녹음 (블로킹)."""
        n_samples = int(duration_seconds * self.SAMPLE_RATE)
        try:
            audio = sd.rec(
                n_samples,
                samplerate=self.SAMPLE_RATE,
                channels=self.CHANNELS,
                dtype=self.DTYPE,
                device=self.device,
            )
            sd.wait()
        except Exception as e:
            raise MicrophoneError(f"고정 시간 녹음 실패: {e}") from e
        return audio.flatten().astype(self.DTYPE)

    # === WAV 저장 (디버그용) ===

    @classmethod
    def save_wav(cls, audio: np.ndarray, path: str) -> dict:
        """numpy array 를 16-bit PCM WAV 로 저장."""
        out = Path(path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            audio_int16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
            with wave.open(str(out), "wb") as wf:
                wf.setnchannels(cls.CHANNELS)
                wf.setsampwidth(2)
                wf.setframerate(cls.SAMPLE_RATE)
                wf.writeframes(audio_int16.tobytes())
        except Exception as e:
            return {"status": "error", "message": f"WAV 저장 실패: {e}"}
        return {"status": "ok", "path": str(out), "size": out.stat().st_size}


# ===========================================================================
# 데모: python -m src.perception.audio_input
# ===========================================================================

def _demo():
    """마이크 디바이스 목록 + Enter PTT 녹음 + 통계 + WAV 저장."""
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    LINE = "=" * 68
    print()
    print(LINE)
    print("  MOD-IN-001 마이크 캡처 데모")
    print(LINE)

    # 디바이스 목록
    print()
    print("  [입력 디바이스 목록]")
    try:
        devices = MicrophoneCapture.list_devices()
        for dev in devices:
            print(f"    [{dev['index']:>2}] {dev['name']}")
            print(f"         channels={dev['channels']}, default_sr={dev['default_samplerate']}Hz")
    except MicrophoneError as e:
        print(f"    [에러] {e}")
        return

    print()
    default = MicrophoneCapture.default_input_device()
    print(f"  [기본 입력] index={default.get('index')}, name={default.get('name')}")

    print()
    print("  " + "─" * 64)

    mic = MicrophoneCapture()

    print()
    print("  마이크 캡처 테스트")
    print("  ─────────────────")
    print("  1. 다음 프롬프트에서 Enter 누르면 녹음 시작")
    print("  2. 마이크에 한국어로 말씀하세요 (예: 안녕하세요 EDDIE)")
    print("  3. 말 끝나면 다시 Enter")
    print()
    input("  Enter to start recording: ")

    try:
        audio = mic.record_until_enter("  녹음 중... Enter로 종료: ")
    except MicrophoneError as e:
        print(f"  [에러] {e}")
        return

    # 신호 분석
    duration = len(audio) / MicrophoneCapture.SAMPLE_RATE
    peak = float(np.abs(audio).max()) if len(audio) > 0 else 0.0
    rms = float(np.sqrt(np.mean(audio ** 2))) if len(audio) > 0 else 0.0

    print()
    print("  [녹음 결과]")
    print(f"    샘플 수    : {len(audio):,}")
    print(f"    길이       : {duration:.2f} 초")
    print(f"    피크 진폭  : {peak:.4f}  (이상적: 0.1 ~ 0.9)")
    print(f"    RMS        : {rms:.4f}")

    if peak < 0.01:
        print()
        print("    ⚠ 신호 너무 약함 — 마이크 볼륨 너무 낮거나 음소거 가능성")
        print("       Windows 설정 → 시스템 → 사운드 → 입력 → 볼륨 확인")
    elif peak > 0.95:
        print()
        print("    ⚠ 클리핑 위험 — 입력이 너무 큼. 마이크에서 떨어져 말씀하거나")
        print("       Windows 입력 볼륨 낮추기")

    # WAV 저장
    import tempfile
    wav_path = Path(tempfile.gettempdir()) / "eddie_mic_test.wav"
    save_result = MicrophoneCapture.save_wav(audio, str(wav_path))
    print()
    if save_result["status"] == "ok":
        print(f"  [WAV 저장] {save_result['path']} ({save_result['size']:,} bytes)")
        print(f"  → Windows 미디어 플레이어로 직접 재생해서 녹음 품질 확인 가능")
    else:
        print(f"  [WAV 저장 실패] {save_result['message']}")

    print()
    print(LINE)
    print("  데모 완료")
    print(LINE)


if __name__ == "__main__":
    _demo()