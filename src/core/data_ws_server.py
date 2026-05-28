"""
data_ws_server.py — 센서 데이터 실시간 전송용 WebSocket 서버

목적: Python 백엔드 → HUD(Electron) 로 센서 데이터를 실시간 push.
      파일 폴링(eddie_data.json)의 지연·경합 없이 즉시 전달.

설계:
  - 백그라운드 스레드에서 asyncio 이벤트 루프 + WebSocket 서버 실행
    → 메인 음성 처리 루프(블로킹)를 방해하지 않음.
  - broadcast(dict) 호출 시 연결된 모든 클라이언트(HUD)에 JSON push.
  - 연결된 HUD가 없어도 조용히 통과 (에러 없음).
  - 음성/자막 채널과 완전 분리 — 센서 데이터 전용.

범위: 127.0.0.1 로컬 전용 (외부 노출 안 함).
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Optional


class DataWebSocketServer:
    """센서 데이터 실시간 브로드캐스트용 로컬 WebSocket 서버."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.host = host
        self.port = port
        self._clients: set = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server = None
        self._started = threading.Event()
        self._last_payload: Optional[str] = None  # 신규 연결에 마지막 상태 즉시 전송

    # ---------------- 공개 API ----------------

    def start(self) -> None:
        """백그라운드 스레드에서 서버 시작 (이미 실행 중이면 무시)."""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        # 서버가 실제로 뜰 때까지 잠깐 대기 (최대 3초)
        self._started.wait(timeout=3.0)

    def broadcast(self, data: dict) -> None:
        """연결된 모든 HUD에 데이터를 push (스레드 안전).

        메인 스레드에서 호출 → 서버 루프에 작업을 안전하게 넘김.
        """
        if not self._loop:
            return
        try:
            payload = json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError):
            return
        self._last_payload = payload
        # 서버 이벤트 루프에 코루틴 제출 (스레드 경계 안전)
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast(payload), self._loop)
        except RuntimeError:
            pass  # 루프가 종료 중이면 조용히 무시

    def stop(self) -> None:
        """서버 종료."""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # ---------------- 내부 ----------------

    def _run_loop(self) -> None:
        """백그라운드 스레드: asyncio 루프 생성 후 서버 실행."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
            self._loop.run_forever()
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                self._loop.close()
            except Exception:  # noqa: BLE001
                pass

    async def _serve(self) -> None:
        import websockets
        self._server = await websockets.serve(self._handler, self.host, self.port)
        self._started.set()

    async def _handler(self, websocket) -> None:
        """클라이언트(HUD) 연결 처리."""
        self._clients.add(websocket)
        try:
            # 신규 연결에 마지막 상태 즉시 전송 (HUD 새로고침 대비)
            if self._last_payload is not None:
                try:
                    await websocket.send(self._last_payload)
                except Exception:  # noqa: BLE001
                    pass
            # 클라이언트가 보내는 메시지는 무시 (단방향 push). 연결 유지만.
            async for _ in websocket:
                pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._clients.discard(websocket)

    async def _broadcast(self, payload: str) -> None:
        if not self._clients:
            return
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)


# ===========================================================================
# 자체 검증
# ===========================================================================

def _demo():
    import sys, time
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    print("WebSocket 서버 시작 (ws://127.0.0.1:8765)")
    srv = DataWebSocketServer()
    srv.start()
    print("연결 대기 중... HUD를 열면 연결됩니다. Ctrl+C로 종료.")
    i = 0
    try:
        while True:
            i += 1
            srv.broadcast({"kind": "value", "values": {"Temperature": 20 + i % 10}})
            print(f"  broadcast #{i} (연결 {srv.client_count})")
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()
        print("종료")


if __name__ == "__main__":
    _demo()
