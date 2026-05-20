"""
MOD-STT-001 — 음성 인식 (Phase 2 Step 2-4)

기술 기반: faster-whisper (Whisper 구현체, CTranslate2 기반)
의도: 한국어 음성을 텍스트로 변환. 로컬 실행으로 API 비용 0원.
입력: numpy ndarray (16kHz mono float32) 또는 WAV/MP3 파일 경로
출력: dict (status, text, language, duration_s, inference_time, segments)
핵심 책임: STT 변환, 모델 lazy load, 한국어 우선

모델 채택 근거 (정혁님 LG PC, RAM 4GB):
  - base (142MB, 약 1GB RAM 사용, int8 양자화)
  - tiny 는 한국어 정확도 부족, small 이상은 RAM 빠듯
  - 나중에 더 좋은 PC로 이식 시 small/medium 으로 업그레이드 가능
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional, Union

import numpy as np


class SpeechToTextError(Exception):
    """STT 변환 실패."""


class SpeechToText:
    """faster-whisper 기반 한국어 음성 인식.

    모델은 첫 호출 시 lazy load. base 모델 약 142MB.
    int8 양자화로 RAM 사용 최소화 (4GB PC 호환).
    """

    SAMPLE_RATE = 16000  # Whisper 표준

    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "auto",
        compute_type: str = "auto",
        language: Optional[str] = "ko",
    ) -> None:
        """
        model_size: tiny / base / small / medium / large-v3
        device: "auto" (GPU 우선, 실패 시 CPU) / "cuda" / "cpu"
        compute_type: "auto" (device에 맞춤) / int8 / float16 / float32
        language: 'ko' (한국어 고정) 또는 None (자동 감지)

        device="auto": CUDA 사용 가능하면 GPU(float16), 아니면 CPU(int8)로 자동 폴백.
        """
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self._model = None  # lazy load
        self._resolved_device = None  # 실제 사용된 device 기록

    @staticmethod
    def _register_cuda_dll_paths() -> None:
        """Windows에서 pip 설치된 nvidia-* 패키지의 DLL 폴더를 등록.
        faster-whisper(ctranslate2)가 cublas64_12.dll, cudnn 등을 찾게 한다.
        CPU 모드거나 비Windows면 아무것도 안 함 (안전)."""
        import sys as _sys
        if _sys.platform != "win32":
            return
        if not hasattr(os, "add_dll_directory"):
            return
        try:
            import site
            import glob
            # site-packages 안의 nvidia/*/bin 폴더들을 DLL 경로로 등록
            search_roots = []
            for sp in site.getsitepackages():
                search_roots.append(sp)
            # venv 환경 대비
            search_roots.append(str(Path(_sys.executable).parent / "Lib" / "site-packages"))
            seen = set()
            for root in search_roots:
                nvidia_dir = Path(root) / "nvidia"
                if not nvidia_dir.exists():
                    continue
                # nvidia/<lib>/bin 패턴
                for bin_dir in glob.glob(str(nvidia_dir / "*" / "bin")):
                    if bin_dir not in seen and Path(bin_dir).exists():
                        try:
                            os.add_dll_directory(bin_dir)
                            seen.add(bin_dir)
                        except OSError:
                            pass
        except Exception:
            # DLL 경로 등록 실패는 치명적 아님 (CPU 폴백 가능)
            pass

    def _ensure_model(self) -> None:
        """첫 호출 시 모델 로드 (처음 실행 시 다운로드 가능)."""
        if self._model is not None:
            return
        # Windows GPU: pip 설치된 nvidia 라이브러리 DLL 경로 등록
        # (cublas64_12.dll 등을 faster-whisper 가 찾도록)
        self._register_cuda_dll_paths()

        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise SpeechToTextError(
                "faster-whisper 미설치. pip install faster-whisper"
            ) from e

        # device/compute_type 결정 (auto면 GPU 우선)
        attempts = []
        if self.device == "auto":
            attempts = [("cuda", "float16"), ("cpu", "int8")]
        elif self.device == "cuda":
            ct = self.compute_type if self.compute_type != "auto" else "float16"
            attempts = [("cuda", ct), ("cpu", "int8")]  # cuda 실패 시 cpu 폴백
        else:
            ct = self.compute_type if self.compute_type != "auto" else "int8"
            attempts = [("cpu", ct)]

        t_start = time.time()
        last_error = None
        for dev, ct in attempts:
            print(f"  Whisper 모델 로드 시도: {self.model_size} ({ct}, {dev})")
            print(f"  (첫 실행 시 모델 다운로드 발생 가능 — 인터넷 필요)")
            try:
                self._model = WhisperModel(
                    self.model_size,
                    device=dev,
                    compute_type=ct,
                )
                self._resolved_device = dev
                elapsed = time.time() - t_start
                print(f"  모델 로드 완료: {dev.upper()} 사용 ({elapsed:.1f}초)")
                if dev == "cpu" and len(attempts) > 1:
                    print(f"  ⚠ GPU 사용 불가 — CPU 폴백. (CUDA/cuDNN 미설치 가능성)")
                return
            except Exception as e:
                last_error = e
                if dev == "cuda":
                    print(f"  GPU 로드 실패, CPU로 폴백합니다: {str(e)[:100]}")
                continue

        raise SpeechToTextError(f"모델 로드 실패 (모든 시도): {last_error}")

    def transcribe(self, audio: Union[np.ndarray, str, Path]) -> dict:
        """
        audio: numpy array (16kHz mono float32) 또는 WAV/MP3 파일 경로
        반환: dict
        """
        self._ensure_model()

        # 입력 정규화
        if isinstance(audio, (str, Path)):
            audio_input: Union[np.ndarray, str] = str(audio)
            if not Path(audio_input).exists():
                return {"status": "error", "message": f"파일 없음: {audio_input}"}
        else:
            if not isinstance(audio, np.ndarray):
                return {"status": "error", "message": "audio 는 numpy.ndarray 또는 파일 경로여야 함"}
            arr = audio
            if arr.ndim != 1:
                arr = arr.flatten()
            if arr.dtype != np.float32:
                arr = arr.astype(np.float32)
            audio_input = arr

        t_start = time.time()
        try:
            segments_iter, info = self._model.transcribe(
                audio_input,
                language=self.language,
                beam_size=5,
                vad_filter=True,  # Whisper 내부 VAD로 침묵 구간 자동 제거
            )

            segments = []
            full_text_parts = []
            for seg in segments_iter:
                segments.append({
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "text": seg.text.strip(),
                })
                full_text_parts.append(seg.text.strip())

            full_text = " ".join(full_text_parts).strip()
        except Exception as e:
            return {"status": "error", "message": f"STT 실패: {e}"}

        elapsed = time.time() - t_start

        return {
            "status": "ok",
            "text": full_text,
            "language": info.language,
            "language_probability": round(float(info.language_probability), 3),
            "duration": round(float(info.duration), 2),
            "inference_time": round(elapsed, 2),
            "segments": segments,
        }


# ===========================================================================
# 데모: python -m src.perception.speech_recognition
# ===========================================================================

def _demo():
    """마이크 캡처 + STT 통합 데모. 단일 또는 반복 모드 선택."""
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    LINE = "=" * 68
    print()
    print(LINE)
    print("  MOD-STT-001 음성 인식 데모 (faster-whisper · medium · cpu)")
    print(LINE)

    # 마이크 import
    try:
        from src.perception.audio_input import MicrophoneCapture, MicrophoneError
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from src.perception.audio_input import MicrophoneCapture, MicrophoneError

    mic = MicrophoneCapture()
    stt = SpeechToText(model_size="medium", device="cpu", language="ko")

    # 모드 선택
    print()
    print("  [데모 모드]")
    print("    1) 단일 발화 — 한 번 녹음하고 변환 후 종료")
    print("    2) 반복 모드 — 계속 받음 (q 입력으로 종료)")
    print()
    mode = (input("  선택 (1/2, 기본 1): ").strip() or "1")

    # 모델 미리 로드 (다운로드 시간 측정용, 첫 호출 시 시간 알림)
    print()
    print("  " + "─" * 64)
    try:
        stt._ensure_model()
    except SpeechToTextError as e:
        print(f"  [에러] {e}")
        return
    print("  " + "─" * 64)

    def one_round() -> bool:
        """한 라운드 진행. False 반환 시 종료."""
        print()
        cmd = input("  Enter 녹음 시작 ('q'+Enter 종료): ").strip()
        if cmd.lower() == "q":
            return False

        try:
            audio = mic.record_until_enter("    녹음 중... Enter로 종료: ")
        except MicrophoneError as e:
            print(f"  [마이크 에러] {e}")
            return True

        duration = len(audio) / MicrophoneCapture.SAMPLE_RATE
        peak = float(np.abs(audio).max()) if len(audio) > 0 else 0.0
        print(f"    녹음 길이: {duration:.2f}초, 피크: {peak:.3f}")

        if peak < 0.01:
            print(f"    ⚠ 신호 약함 — 마이크 볼륨 확인. STT 결과 부정확할 수 있음.")

        print(f"    STT 변환 중...")
        result = stt.transcribe(audio)
        if result["status"] != "ok":
            print(f"    [STT 에러] {result.get('message')}")
            return True

        print()
        print(f"  ┌─ EDDIE 가 들은 내용 ───────────────────────────")
        print(f"  │ \"{result['text']}\"")
        print(f"  └─────────────────────────────────────────────")
        print(f"    언어: {result['language']} (확률 {result['language_probability']})")
        print(f"    음성 길이: {result['duration']}초, 추론 시간: {result['inference_time']}초")
        if len(result.get("segments", [])) > 1:
            print(f"    세그먼트: {len(result['segments'])}개")
        return True

    if mode == "2":
        while one_round():
            pass
    else:
        one_round()

    print()
    print(LINE)
    print("  데모 종료")
    print(LINE)


if __name__ == "__main__":
    _demo()