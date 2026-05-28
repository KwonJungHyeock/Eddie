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


import re as _re
import threading as _threading
import queue as _queue


def _split_sentences(text: str) -> list[str]:
    """응답 텍스트를 문장 단위로 분할 (한국어/영어 종결부호·줄바꿈 기준)."""
    text = (text or "").strip()
    if not text:
        return []
    parts = _re.split(r"(?<=[.!?。\n])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def _process_turn(audio, stt, core, tts, bus, log) -> None:
    """녹음된 오디오 한 건을 STT→추론→TTS(문장 스트리밍) 처리."""
    import time as _t  # 구간 측정용
    peak = float(np.abs(audio).max()) if len(audio) > 0 else 0.0
    if peak < 0.01:
        log("  ⚠ 신호 약함 — 다시 시도해주세요.")
        bus.set_state("idle")
        return

    # STT → thinking
    bus.set_state("thinking")
    log("  음성 인식 중...")
    _t0 = _t.perf_counter()
    stt_result = stt.transcribe(audio)
    _t_stt = _t.perf_counter() - _t0
    if stt_result["status"] != "ok" or not stt_result["text"]:
        log("  [인식 실패] 다시 시도해주세요.")
        bus.set_state("idle")
        return

    user_text = stt_result["text"]
    log(f"  정혁님: \"{user_text}\"")

    # LLM 추론
    _t1 = _t.perf_counter()
    eddie_text = core.chat(user_text)
    _t_llm = _t.perf_counter() - _t1
    log(f"  에디: \"{eddie_text}\"")

    # === 문장 단위 스트리밍 재생 ===
    # 핵심: 첫 문장이 합성되는 즉시 재생 시작 → 나머지는 백그라운드로 선행 합성.
    # 그래서 "전체 합성을 기다리는" 대기(노란색)가 거의 사라진다.
    sentences = _split_sentences(eddie_text)
    if not sentences:
        bus.set_state("idle")
        return

    log("  음성 합성/재생 중 (스트리밍)...")
    _t2 = _t.perf_counter()

    # 선행 합성 워커: 문장을 순서대로 합성해 큐에 넣음
    synth_q: "_queue.Queue" = _queue.Queue()

    def _synth_worker():
        for s in sentences:
            try:
                r = tts.synthesize_only(s)
                synth_q.put((s, r))
            except Exception as e:  # noqa: BLE001
                synth_q.put((s, {"status": "error", "message": str(e)}))
        synth_q.put(None)  # 종료 신호

    _threading.Thread(target=_synth_worker, daemon=True).start()

    first = True
    idx = 0
    while True:
        item = synth_q.get()
        if item is None:
            break
        sentence, r = item
        idx += 1
        if r.get("status") != "ok":
            log(f"  [TTS 에러] {r.get('message')}")
            continue
        if first:
            _t_first = _t.perf_counter() - _t2
            log(f"  ⏱  STT {_t_stt:.2f}s | LLM {_t_llm:.2f}s | 첫음성까지 {_t_first:.2f}s")
            first = False
        # 자막 발행: 첫 문장은 새로, 이후는 누적(append). HUD가 append/replace 구분.
        bus.set_state("speaking", detail={
            "text": sentence,
            "duration": r.get("duration", 0.0),
            "append": idx > 1,
        })
        try:
            tts.play_file(r["path"])  # 블로킹 재생 (이 사이 다음 문장은 선행 합성됨)
        except Exception as e:  # noqa: BLE001
            log(f"  [재생 에러] {e}")
        finally:
            # 재생 끝난 임시 파일 정리 (고유명이라 누적 방지)
            try:
                import os as _os
                _os.unlink(r["path"])
            except OSError:
                pass

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
        core.close()  # 자원 정리
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
        core.close()  # 브라우저 등 자원 정리

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
        core.close()  # 자원 정리
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
        core.close()  # 브라우저 등 자원 정리

    print("\n  종료. 정혁님, 수고하셨습니다.")


def main():
    if "--gui" in sys.argv:
        run_gui_mode()
    else:
        run_console_mode()


if __name__ == "__main__":
    main()
