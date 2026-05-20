"""
voice_chat.py — EDDIE 음성 챗 루프 (Phase 2 Step 2-5 + Phase 3 GUI 연동)

통합 파이프라인:
  MOD-IN-001 (마이크) → MOD-STT-001 (Whisper large-v3) →
  MOD-LLM-001 (EddieCore) → MOD-TTS-001 (jarvis-pro)

GUI 연동 (Phase 3-3):
  각 단계에서 StateBus 에 상태 발행 → HUD(Electron)가 읽고 화면 전환.
  idle(대기) → listening(녹음) → thinking(STT+추론) → speaking(TTS)

실행: python voice_chat.py
종료: 녹음 프롬프트에서 'q' + Enter
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.perception.audio_input import MicrophoneCapture, MicrophoneError
from src.perception.speech_recognition import SpeechToText, SpeechToTextError
from src.core.reasoning_engine import EddieCore
from src.output.text_to_speech import TextToSpeech
from src.core.state_bus import FileStateBus

import numpy as np


def main() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    LINE = "=" * 64
    print()
    print(LINE)
    print("  EDDIE 음성 챗 루프 (GUI 연동)")
    print(LINE)
    print("  마이크 → Whisper → EDDIE → 음성 응답")
    print("  종료: 녹음 프롬프트에서 'q' + Enter")
    print(LINE)

    # 컴포넌트 초기화
    print("\n  컴포넌트 초기화 중...")
    mic = MicrophoneCapture()
    stt = SpeechToText(model_size="medium", device="cpu", language="ko")
    core = EddieCore()
    tts = TextToSpeech()  # 기본 프리셋 jarvis-pro

    # 상태 버스 (GUI 동기화)
    bus = FileStateBus()
    print(f"  상태 파일: {bus.get_path()}")
    bus.set_state("idle")

    print("  Whisper 모델 로드 중 (잠시만)...")
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

            # 1. 녹음 → listening
            bus.set_state("listening")
            try:
                audio = mic.record_until_enter("      녹음 중... Enter로 종료: ")
            except MicrophoneError as e:
                print(f"  [마이크 에러] {e}")
                bus.set_state("idle")
                continue

            peak = float(np.abs(audio).max()) if len(audio) > 0 else 0.0
            if peak < 0.01:
                print("      ⚠ 신호 약함 — 다시 시도해주세요.")
                bus.set_state("idle")
                continue

            # 2. STT + 추론 → thinking
            bus.set_state("thinking")
            print("      음성 인식 중...")
            stt_result = stt.transcribe(audio)
            if stt_result["status"] != "ok" or not stt_result["text"]:
                print("      [인식 실패] 다시 시도해주세요.")
                bus.set_state("idle")
                continue

            user_text = stt_result["text"]
            print(f"      정혁님: \"{user_text}\"")

            eddie_text = core.chat(user_text)
            print(f"      에디: \"{eddie_text}\"")

            # 3. TTS → speaking
            bus.set_state("speaking", detail={"text": eddie_text})
            print("      음성 합성 중...")
            tts_result = tts.speak(eddie_text)
            if tts_result["status"] != "ok":
                print(f"      [TTS 에러] {tts_result.get('message')}")

            bus.set_state("idle")
    finally:
        bus.set_state("idle")

    print()
    print(LINE)
    print("  EDDIE 음성 챗 종료. 정혁님, 수고하셨습니다.")
    print(LINE)


if __name__ == "__main__":
    main()