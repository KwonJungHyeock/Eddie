"""
voice_chat.py — EDDIE 음성 챗 루프 (Phase 3-4 풀 통합)

GUI 통합 흐름:
  HUD 스페이스바 → CommandBus(record) → 이 루프가 폴링 감지 → 녹음
  녹음 → STT(medium) → EddieCore → TTS(jarvis-pro)
  각 단계 → StateBus 발행 → HUD 자동 상태 전환

두 가지 실행 모드:
  --gui   : HUD 연동 모드. CommandBus 폴링으로 HUD 스페이스바 명령 대기.
  (없음)  : 콘솔 모드. 기존처럼 Enter 로 녹음 (단독 디버깅용).

녹음 종료 방식 (GUI 모드):
  스페이스바 1회 → 녹음 시작 / 다시 1회 → 종료 (토글)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.perception.audio_input import MicrophoneCapture, MicrophoneError
from src.perception.speech_recognition import SpeechToText, SpeechToTextError
from src.core.reasoning_engine import EddieCore
from src.output.text_to_speech import TextToSpeech
from src.core.state_bus import FileStateBus
from src.core.command_bus import FileCommandBus

import numpy as np


def _init_stdout():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass


def _process_turn(audio, stt, core, tts, bus, log) -> None:
    """녹음된 오디오 한 건을 STT→추론→TTS 처리."""
    peak = float(np.abs(audio).max()) if len(audio) > 0 else 0.0
    if peak < 0.01:
        log("  ⚠ 신호 약함 — 다시 시도해주세요.")
        bus.set_state("idle")
        return

    # STT + 추론 → thinking
    bus.set_state("thinking")
    log("  음성 인식 중...")
    stt_result = stt.transcribe(audio)
    if stt_result["status"] != "ok" or not stt_result["text"]:
        log("  [인식 실패] 다시 시도해주세요.")
        bus.set_state("idle")
        return

    user_text = stt_result["text"]
    log(f"  정혁님: \"{user_text}\"")

    eddie_text = core.chat(user_text)
    log(f"  에디: \"{eddie_text}\"")

    # TTS: 먼저 합성(재생 X) + 길이 측정
    log("  음성 합성 중...")
    tts_result = tts.synthesize_only(eddie_text)
    if tts_result["status"] != "ok":
        log(f"  [TTS 에러] {tts_result.get('message')}")
        bus.set_state("idle")
        return

    duration = tts_result.get("duration", 0.0)

    # 자막 발행(duration 포함) + 음성 재생 동시 시작
    # HUD는 duration에 맞춰 자막을 타이핑 → 음성과 함께 끝남
    bus.set_state("speaking", detail={"text": eddie_text, "duration": duration})
    try:
        tts.play_file(tts_result["path"])  # 블로킹 재생
    except Exception as e:
        log(f"  [재생 에러] {e}")

    bus.set_state("idle")


def run_gui_mode():
    """HUD 연동 모드. CommandBus(스페이스바) 폴링."""
    _init_stdout()
    LINE = "=" * 60
    print(LINE)
    print("  EDDIE 음성 챗 — GUI 연동 모드")
    print(LINE)
    print("  HUD 창에서 스페이스바로 녹음 시작/종료")
    print("  (이 창은 로그 표시용, 닫지 마세요)")
    print(LINE)

    print("\n  컴포넌트 초기화 중...")
    mic = MicrophoneCapture()
    stt = SpeechToText(model_size="large-v3", device="auto", language="ko")
    core = EddieCore()
    tts = TextToSpeech()
    bus = FileStateBus()
    cmd_bus = FileCommandBus()
    cmd_bus.sync_seq()  # 과거 명령 무시

    bus.set_state("idle")
    print("  Whisper 모델 로드 중...")
    try:
        stt._ensure_model()
    except SpeechToTextError as e:
        print(f"  [에러] {e}")
        return
    print("  준비 완료. HUD 에서 스페이스바를 누르세요.\n")

    recording = False
    rec_chunks = []
    stream = None

    import sounddevice as sd

    def log(msg):
        print(msg)

    try:
        while True:
            command = cmd_bus.poll_command()

            if command:
                cmd = command["command"]
                if cmd == "stop":
                    break
                if cmd == "record":
                    if not recording:
                        # 녹음 시작
                        recording = True
                        rec_chunks = []
                        bus.set_state("listening")
                        log("  ● 녹음 시작...")
                        stream = sd.InputStream(
                            samplerate=mic.SAMPLE_RATE,
                            channels=mic.CHANNELS,
                            dtype=mic.DTYPE,
                            device=mic.device,
                            blocksize=1024,
                            callback=lambda indata, frames, t, s: rec_chunks.append(indata.copy()),
                        )
                        stream.start()
                    else:
                        # 녹음 종료
                        recording = False
                        if stream:
                            stream.stop()
                            stream.close()
                            stream = None
                        log("  ■ 녹음 종료. 처리 중...")
                        if rec_chunks:
                            audio = np.concatenate(rec_chunks, axis=0).flatten().astype(mic.DTYPE)
                            _process_turn(audio, stt, core, tts, bus, log)
                        else:
                            bus.set_state("idle")

            time.sleep(0.05)  # 50ms 폴링
    except KeyboardInterrupt:
        pass
    finally:
        if stream:
            try:
                stream.stop(); stream.close()
            except Exception:
                pass
        bus.set_state("idle")

    print("\n  EDDIE 음성 챗 종료.")


def run_console_mode():
    """콘솔 단독 모드 (기존 Enter 방식, 디버깅용)."""
    _init_stdout()
    LINE = "=" * 60
    print(LINE)
    print("  EDDIE 음성 챗 — 콘솔 모드 (Enter 녹음)")
    print(LINE)

    mic = MicrophoneCapture()
    stt = SpeechToText(model_size="large-v3", device="auto", language="ko")
    core = EddieCore()
    tts = TextToSpeech()
    bus = FileStateBus()
    bus.set_state("idle")

    print("  Whisper 모델 로드 중...")
    try:
        stt._ensure_model()
    except SpeechToTextError as e:
        print(f"  [에러] {e}")
        return
    print("  준비 완료.\n")

    turn = 0
    try:
        while True:
            turn += 1
            bus.set_state("idle")
            print(LINE)
            cmd = input(f"  [{turn}] Enter 녹음 시작 ('q' 종료): ").strip()
            if cmd.lower() == "q":
                break
            bus.set_state("listening")
            try:
                audio = mic.record_until_enter("    녹음 중... Enter로 종료: ")
            except MicrophoneError as e:
                print(f"  [마이크 에러] {e}")
                bus.set_state("idle")
                continue
            _process_turn(audio, stt, core, tts, bus, print)
    finally:
        bus.set_state("idle")

    print("\n  종료. 정혁님, 수고하셨습니다.")


def main():
    if "--gui" in sys.argv:
        run_gui_mode()
    else:
        run_console_mode()


if __name__ == "__main__":
    main()
