"""
voice_chat.py — EDDIE 음성 챗 루프 (Phase 2 Step 2-5)

통합 파이프라인:
  MOD-IN-001 (마이크) → MOD-STT-001 (Whisper) →
  MOD-LLM-001 (EddieCore, MOCK) → MOD-TTS-001 (Edge-TTS jarvis-1)

실행: python voice_chat.py
동작: Enter 녹음 시작 → 말하기 → Enter 종료 → EDDIE 음성 응답 → 반복
종료: 녹음 프롬프트에서 'q' + Enter

⚠ 응답 내용은 현재 MOCK. 결제 후 .env 의 EDDIE_MOCK_MODE=false 로 전환 시 실제 Claude.
"""

from __future__ import annotations

import sys
from pathlib import Path

# 프로젝트 루트를 path 에 추가
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.perception.audio_input import MicrophoneCapture, MicrophoneError
from src.perception.speech_recognition import SpeechToText, SpeechToTextError
from src.core.reasoning_engine import EddieCore
from src.output.text_to_speech import TextToSpeech

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
    print("  EDDIE 음성 챗 루프 (Phase 2 Step 2-5)")
    print(LINE)
    print("  마이크 → Whisper → EDDIE → 음성 응답")
    print("  종료: 녹음 프롬프트에서 'q' + Enter")
    print(LINE)

    # 컴포넌트 초기화
    print("\n  컴포넌트 초기화 중...")
    mic = MicrophoneCapture()
    stt = SpeechToText(model_size="large-v3", device="cpu", language="ko")
    core = EddieCore()
    tts = TextToSpeech()  # 기본 프리셋 jarvis-1

    print("  Whisper 모델 로드 중 (잠시만)...")
    try:
        stt._ensure_model()
    except SpeechToTextError as e:
        print(f"  [에러] {e}")
        return
    print("  준비 완료.\n")

    turn = 0
    while True:
        turn += 1
        print(LINE)
        cmd = input(f"  [{turn}] Enter 녹음 시작 ('q' 종료): ").strip()
        if cmd.lower() == "q":
            break

        # 1. 녹음
        try:
            audio = mic.record_until_enter("      녹음 중... Enter로 종료: ")
        except MicrophoneError as e:
            print(f"  [마이크 에러] {e}")
            continue

        peak = float(np.abs(audio).max()) if len(audio) > 0 else 0.0
        if peak < 0.01:
            print("      ⚠ 신호 약함 — 다시 시도해주세요.")
            continue

        # 2. STT
        print("      음성 인식 중...")
        stt_result = stt.transcribe(audio)
        if stt_result["status"] != "ok" or not stt_result["text"]:
            print("      [인식 실패] 다시 시도해주세요.")
            continue

        user_text = stt_result["text"]
        print(f"      정혁님: \"{user_text}\"")

        # 3. EDDIE 추론 (현재 MOCK)
        eddie_text = core.chat(user_text)
        print(f"      에디: \"{eddie_text}\"")

        # 4. TTS 음성 응답
        print("      음성 합성 중...")
        tts_result = tts.speak(eddie_text)
        if tts_result["status"] != "ok":
            print(f"      [TTS 에러] {tts_result.get('message')}")

    print()
    print(LINE)
    print("  EDDIE 음성 챗 종료. 정혁님, 수고하셨습니다.")
    print(LINE)


if __name__ == "__main__":
    main()
