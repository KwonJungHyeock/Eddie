"""
MOD-TTS-001 — 음성 합성 + 오디오 후처리 (Step 2-3 미세 조정판)

기술 기반: Microsoft Edge-TTS + ffmpeg 필터 체인
의도: 자비스 톤에 가까운 한국어 AI 비서 목소리 구현.
      정혁님 피드백 (2026-05-20):
        - low 프리셋이 한국어에서 가장 자연스러움
        - 기본 속도 느림 → 자연스러운 속도로 조정 필요
        - 자비스 목소리(폴 베타니)에 가깝게 — 한국어 TTS 한계 내에서 최선

3가지 자비스 톤 변형:
  - "jarvis-1" : low + rate +15%
  - "jarvis-2" : low + rate +12% + pitch -2Hz       ← 권장 기본값
  - "jarvis-3" : low + rate +15% + pitch -3Hz + 저음 보강

추가 프리셋 (비교용):
  - "raw"      : 후처리 없음, 기본 속도
  - "low"      : 가벼운 후처리, 기본 속도
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional


class TextToSpeechError(Exception):
    """TTS 도구 동작 실패."""


# ===========================================================================
# 후처리 필터 체인 (ffmpeg)
# ===========================================================================

FILTER_LOW = (
    "equalizer=f=120:width_type=h:width=80:g=1.5,"
    "equalizer=f=1500:width_type=h:width=600:g=-1,"
    "aecho=0.9:0.4:50:0.2,"
    "loudnorm=I=-16:TP=-1.5:LRA=11"
)
FILTER_JARVIS_BOOST = (
    # jarvis-3 전용 - low 기반 + 저음 살짝 더 부스트
    "equalizer=f=100:width_type=h:width=80:g=2.5,"
    "equalizer=f=1500:width_type=h:width=600:g=-1.2,"
    "aecho=0.9:0.42:55:0.22,"
    "loudnorm=I=-16:TP=-1.5:LRA=11"
)


# ===========================================================================
# 프리셋 정의 — (rate, pitch, filter_chain)
# ===========================================================================

PRESETS = {
    "raw":       {"rate": "+0%",  "pitch": "+0Hz",  "filter": None},
    "low":       {"rate": "+0%",  "pitch": "+0Hz",  "filter": FILTER_LOW},
    "jarvis-1":  {"rate": "+15%", "pitch": "+0Hz",  "filter": FILTER_LOW},
    "jarvis-2":  {"rate": "+12%", "pitch": "-2Hz",  "filter": FILTER_LOW},
    "jarvis-3":  {"rate": "+15%", "pitch": "-3Hz",  "filter": FILTER_JARVIS_BOOST},
}


class TextToSpeech:
    """Edge-TTS + ffmpeg 후처리 기반 음성 합성 도구.

    음성 프로필: ko-KR-InJoonNeural (ARC-001 v0.3 / DSN-001 v0.1 결정)
    후처리 기본값: jarvis-2 (정혁님 피드백 기반 권장 프리셋)
    """

    DEFAULT_VOICE = "ko-KR-InJoonNeural"
    AVAILABLE_VOICES_KO = (
        "ko-KR-InJoonNeural",
        "ko-KR-SunHiNeural",
        "ko-KR-BongJinNeural",
        "ko-KR-GookMinNeural",
    )
    DEFAULT_PRESET = "jarvis-1"

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        preset: str = DEFAULT_PRESET,
        volume: str = "+0%",
    ) -> None:
        if voice not in self.AVAILABLE_VOICES_KO and not voice.startswith("ko-KR-"):
            raise TextToSpeechError(
                f"지원하지 않는 음성: {voice}. "
                f"한국어 옵션: {self.AVAILABLE_VOICES_KO}"
            )
        if preset not in PRESETS:
            raise TextToSpeechError(
                f"지원하지 않는 프리셋: {preset}. 옵션: {list(PRESETS.keys())}"
            )
        self.voice = voice
        self.preset = preset
        self.volume = volume

    def synthesize(
        self,
        text: str,
        output_path: str,
        preset: Optional[str] = None,
    ) -> dict:
        """텍스트를 MP3로 합성 + 후처리."""
        text = (text or "").strip()
        if not text:
            return {"status": "error", "message": "빈 텍스트 거부"}

        chosen = preset or self.preset
        if chosen not in PRESETS:
            return {"status": "error", "message": f"알 수 없는 프리셋: {chosen}"}

        spec = PRESETS[chosen]
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)

        # 1단계: Edge-TTS 합성 (rate/pitch 적용)
        raw_tmp = Path(tempfile.gettempdir()) / f"eddie_tts_raw_{os.getpid()}.mp3"
        try:
            self._run_async(self._async_synthesize(
                text, str(raw_tmp), spec["rate"], spec["pitch"]
            ))
        except Exception as e:
            return {"status": "error", "message": f"Edge-TTS 합성 실패: {e}"}

        if not raw_tmp.exists() or raw_tmp.stat().st_size == 0:
            return {"status": "error", "message": "raw 음성 결과가 비어있음"}

        # 2단계: 후처리
        filter_chain = spec["filter"]
        if filter_chain is None:
            try:
                import shutil
                shutil.copy(str(raw_tmp), str(out))
            except Exception as e:
                return {"status": "error", "message": f"파일 복사 실패: {e}"}
        else:
            try:
                self._apply_ffmpeg_filters(str(raw_tmp), str(out), filter_chain)
            except subprocess.CalledProcessError as e:
                return {
                    "status": "error",
                    "message": f"ffmpeg 실패 (exit {e.returncode}): "
                               f"{e.stderr.decode('utf-8', errors='replace')[:200] if e.stderr else ''}"
                }
            except FileNotFoundError:
                return {"status": "error", "message": "ffmpeg 실행 파일을 찾을 수 없음"}

        try:
            raw_tmp.unlink()
        except OSError:
            pass

        if not out.exists() or out.stat().st_size == 0:
            return {"status": "error", "message": "최종 출력이 비어있음"}

        return {
            "status": "ok",
            "text": text[:80] + ("..." if len(text) > 80 else ""),
            "voice": self.voice,
            "preset": chosen,
            "rate": spec["rate"],
            "pitch": spec["pitch"],
            "path": str(out),
            "size": out.stat().st_size,
        }

    def speak(self, text: str, preset: Optional[str] = None) -> dict:
        """합성 + 후처리 후 즉시 재생."""
        tmp_path = Path(tempfile.gettempdir()) / f"eddie_tts_{os.getpid()}.mp3"
        result = self.synthesize(text, str(tmp_path), preset=preset)
        if result["status"] != "ok":
            return result
        try:
            self._play_audio(str(tmp_path))
            result["played"] = True
        except Exception as e:
            result["played"] = False
            result["play_error"] = str(e)
        return result

    # === 내부 메서드 ===

    @staticmethod
    def _run_async(coro):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import nest_asyncio  # type: ignore
                nest_asyncio.apply()
                loop.run_until_complete(coro)
            else:
                loop.run_until_complete(coro)
        except RuntimeError:
            asyncio.run(coro)

    async def _async_synthesize(
        self, text: str, output_path: str, rate: str, pitch: str
    ) -> None:
        import edge_tts
        communicate = edge_tts.Communicate(
            text=text,
            voice=self.voice,
            rate=rate,
            pitch=pitch,
            volume=self.volume,
        )
        await communicate.save(output_path)

    @staticmethod
    def _apply_ffmpeg_filters(input_path: str, output_path: str, filter_chain: str) -> None:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", input_path, "-af", filter_chain,
            "-acodec", "libmp3lame", "-b:a", "128k",
            output_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)

    @staticmethod
    def _play_audio(path: str) -> None:
        try:
            subprocess.run(
                ["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet", path],
                check=True,
            )
            return
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        from pydub import AudioSegment
        from pydub.playback import play
        audio = AudioSegment.from_mp3(path)
        play(audio)


# ===========================================================================
# 데모 — 3가지 자비스 변형 + low 기준점 비교
# ===========================================================================

def _demo():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    LINE = "=" * 70

    # 사용자 인자: 직접 텍스트 지정 시 jarvis-2 (기본)로 재생
    if len(sys.argv) > 1:
        user_text = " ".join(sys.argv[1:])
        print()
        print(LINE)
        print(f"  사용자 입력 (jarvis-2 기본 프리셋): {user_text}")
        print(LINE)
        tts = TextToSpeech()
        print("  재생 시작...")
        result = tts.speak(user_text)
        for k, v in result.items():
            print(f"  {k}: {v}")
        return

    # 기본 시연: 4가지 비교
    test_text = (
        "정혁님, EDDIE입니다. "
        "자비스 톤 미세 조정 결과를 확인해 보시고, "
        "가장 마음에 드는 프리셋을 알려주십시오."
    )

    print()
    print(LINE)
    print("  MOD-TTS-001 Step 2-3 미세 조정 — 자비스 톤 비교")
    print(LINE)
    print(f"  테스트 문장: {test_text}")
    print()
    print("  비교 순서:")
    print("    (1) LOW      — 기준점 (이전 선택, 속도 느림)")
    print("    (2) JARVIS-1 — low + 빠른 속도 (+15%)")
    print("    (3) JARVIS-2 — low + 속도 (+12%) + 피치 약간 낮음 ★ 권장 기본")
    print("    (4) JARVIS-3 — 더 빠르고 더 깊은 음색")
    print(LINE)

    presets_in_order = [
        ("low",      "(1) LOW       — 기준점 (이전 선택, 속도 느림)"),
        ("jarvis-1", "(2) JARVIS-1  — low + 자연 속도 (+15%)"),
        ("jarvis-2", "(3) JARVIS-2  — low + 자연 속도 (+12%) + 깊이 (-2Hz)  ★ 권장"),
        ("jarvis-3", "(4) JARVIS-3  — 더 빠른 (+15%) + 더 깊은 (-3Hz) + 저음 보강"),
    ]

    tts = TextToSpeech()

    for preset_name, description in presets_in_order:
        print()
        print(description)
        spec = PRESETS[preset_name]
        print(f"    파라미터: rate={spec['rate']}, pitch={spec['pitch']}")
        print("    재생 중...")
        result = tts.speak(test_text, preset=preset_name)
        if result["status"] != "ok":
            print(f"    [에러] {result.get('message', '알 수 없음')}")
            break
        print(f"    완료. 파일 크기: {result['size']:,} bytes")
        import time
        time.sleep(0.7)

    print()
    print(LINE)
    print("  데모 완료. 마음에 드는 프리셋을 알려주세요.")
    print("  추가 미세 조정도 가능합니다 (예: 속도 더 빠르게, 피치 더 낮게).")
    print(LINE)


if __name__ == "__main__":
    _demo()