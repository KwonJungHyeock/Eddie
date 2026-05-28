"""
MOD-TTS-001 — 음성 합성 + 오디오 후처리 (완전판)

기술 기반: Microsoft Edge-TTS + ffmpeg 필터 체인
음성 프로필: ko-KR-InJoonNeural
기본 프리셋: jarvis-1 (정혁님 확정 — low 후처리 + 빠른 속도)

프리셋:
  - raw      : 후처리 없음, 기본 속도
  - low      : 가벼운 후처리, 기본 속도
  - jarvis-1 : low + rate +30% (EDDIE 기본 ★)
  - jarvis-2 : low + rate +12% + pitch -2Hz
  - jarvis-3 : low + rate +15% + pitch -3Hz + 저음 보강

TTS 정제: 영문 EDDIE→에디, (MOCK)/(SYSTEM) 등 시스템 태그 제거
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional


class TextToSpeechError(Exception):
    """TTS 도구 동작 실패."""


FILTER_LOW = (
    "equalizer=f=120:width_type=h:width=80:g=1.5,"
    "equalizer=f=1500:width_type=h:width=600:g=-1,"
    "aecho=0.9:0.4:50:0.2,"
    "loudnorm=I=-16:TP=-1.5:LRA=11"
)
FILTER_JARVIS_BOOST = (
    "equalizer=f=100:width_type=h:width=80:g=2.5,"
    "equalizer=f=1500:width_type=h:width=600:g=-1.2,"
    "aecho=0.9:0.42:55:0.22,"
    "loudnorm=I=-16:TP=-1.5:LRA=11"
)

PRESETS = {
    "raw":       {"rate": "+0%",  "pitch": "+0Hz",  "filter": None},
    "low":       {"rate": "+0%",  "pitch": "+0Hz",  "filter": FILTER_LOW},
    "jarvis-1":  {"rate": "+30%", "pitch": "+0Hz",  "filter": FILTER_LOW},
    "jarvis-pro": {"rate": "+25%", "pitch": "-3Hz", "filter": None},
    "jarvis-2":  {"rate": "+12%", "pitch": "-2Hz",  "filter": FILTER_LOW},
    "jarvis-3":  {"rate": "+15%", "pitch": "-3Hz",  "filter": FILTER_JARVIS_BOOST},
}


class TextToSpeech:
    """Edge-TTS + ffmpeg 후처리 기반 음성 합성 도구."""

    DEFAULT_VOICE = "ko-KR-InJoonNeural"
    AVAILABLE_VOICES_KO = (
        "ko-KR-InJoonNeural",
        "ko-KR-SunHiNeural",
        "ko-KR-BongJinNeural",
        "ko-KR-GookMinNeural",
    )
    DEFAULT_PRESET = "jarvis-pro"

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        preset: str = DEFAULT_PRESET,
        volume: str = "+0%",
    ) -> None:
        if voice not in self.AVAILABLE_VOICES_KO and not voice.startswith("ko-KR-"):
            raise TextToSpeechError(
                f"지원하지 않는 음성: {voice}. 한국어 옵션: {self.AVAILABLE_VOICES_KO}"
            )
        if preset not in PRESETS:
            raise TextToSpeechError(
                f"지원하지 않는 프리셋: {preset}. 옵션: {list(PRESETS.keys())}"
            )
        self.voice = voice
        self.preset = preset
        self.volume = volume

    @staticmethod
    def _clean_for_speech(text: str) -> str:
        """TTS 발음 교정: 영문 EDDIE->에디, 시스템 태그 제거."""
        text = re.sub(r"\((MOCK|SYSTEM|REAL|DEBUG)\)\s*", "", text)
        text = re.sub(r"EDDIE", "에디", text, flags=re.IGNORECASE)
        return text.strip()

    def synthesize(
        self,
        text: str,
        output_path: str,
        preset: Optional[str] = None,
    ) -> dict:
        text = (text or "").strip()
        if not text:
            return {"status": "error", "message": "빈 텍스트 거부"}

        text = self._clean_for_speech(text)

        chosen = preset or self.preset
        if chosen not in PRESETS:
            return {"status": "error", "message": f"알 수 없는 프리셋: {chosen}"}

        spec = PRESETS[chosen]
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)

        import uuid as _uuid
        raw_tmp = (
            Path(tempfile.gettempdir())
            / f"eddie_tts_raw_{os.getpid()}_{_uuid.uuid4().hex[:8]}.mp3"
        )
        try:
            self._run_async(self._async_synthesize(
                text, str(raw_tmp), spec["rate"], spec["pitch"]
            ))
        except Exception as e:
            return {"status": "error", "message": f"Edge-TTS 합성 실패: {e}"}

        if not raw_tmp.exists() or raw_tmp.stat().st_size == 0:
            return {"status": "error", "message": "raw 음성 결과가 비어있음"}

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
    def _get_duration(path: str) -> float:
        """ffprobe 로 오디오 길이(초) 측정. 실패 시 0.0."""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, check=True,
            )
            return float(result.stdout.strip())
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
            return 0.0

    def synthesize_only(self, text: str, preset: Optional[str] = None) -> dict:
        """합성만 하고 재생 안 함. 파일 경로 + duration 반환.
        (자막-음성 동기화용: 호출부가 자막 발행과 재생을 동시에 시작)

        ⚠ 매 호출마다 고유 파일명 사용 — 문장 단위 선행 합성(스트리밍) 시
           여러 문장이 같은 파일을 덮어써 재생이 깨지는 것을 방지한다.
        """
        import uuid
        tmp_path = (
            Path(tempfile.gettempdir())
            / f"eddie_tts_{os.getpid()}_{uuid.uuid4().hex[:8]}.mp3"
        )
        result = self.synthesize(text, str(tmp_path), preset=preset)
        if result["status"] == "ok":
            result["duration"] = self._get_duration(result["path"])
        return result

    def play_file(self, path: str) -> None:
        """이미 합성된 파일을 재생 (블로킹)."""
        self._play_audio(path)

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


def _demo():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    LINE = "=" * 68

    if len(sys.argv) > 1:
        user_text = " ".join(sys.argv[1:])
        print(f"\n  입력 (jarvis-1): {user_text}")
        tts = TextToSpeech()
        print("  재생 중...")
        result = tts.speak(user_text)
        for k, v in result.items():
            print(f"  {k}: {v}")
        return

    test_text = "정혁님, 검색을 완료했습니다. 결과는 세 건입니다."
    print()
    print(LINE)
    print("  MOD-TTS-001 데모 (기본 프리셋 jarvis-pro, raw + rate +28%)")
    print(LINE)
    print(f"  문장: {test_text}")
    print("  재생 중...")
    tts = TextToSpeech()
    result = tts.speak(test_text)
    for k, v in result.items():
        print(f"  {k}: {v}")
    print(LINE)
    print("  데모 완료")
    print(LINE)


if __name__ == "__main__":
    _demo()
