"""
MOD-LLM-001 — 추론 엔진 (Reasoning Engine)

기술 기반: OpenAI API (Chat Completions) + Function Calling
의도: 사용자 의도 분석부터 응답 합성까지 단일 LLM이 일관되게 수행
입력: 사용자 발화 텍스트, 시스템 프롬프트, 대화 컨텍스트, 도구 실행 결과
출력: 도구 호출 명령 또는 최종 응답 텍스트
핵심 책임: Intent classification, Tool selection, Response synthesis, Multi-turn context 관리

Phase 1 구현 단계:
  Step 1-2 (완료): MOCK 모드 응답 생성기.
  Step 1-3 (완료): 실제 OpenAI API 호출 (도구 없는 기본 chat).
  Step 2a (현재): 웹 검색 도구(MOD-TOL-001) Function Calling 연결.
  Step 2b~ (예정): 파일 조작·브라우저 제어 도구 추가.
"""

import json
import os
import sys
from pathlib import Path


class EddieCore:
    """EDDIE의 추론 엔진.

    환경변수 EDDIE_MOCK_MODE 값에 따라 MOCK 또는 REAL 모드로 동작.
      true  → MOCK 모드 (가짜 응답, API 호출 없음)
      false → REAL 모드 (OpenAI API 호출)
    """

    def __init__(self) -> None:
        # 모드 결정
        mock_env = os.getenv("EDDIE_MOCK_MODE", "true").strip().lower()
        self.mock_mode: bool = mock_env in ("true", "1", "yes", "on")

        # 사용자 호칭 (페르소나 정의서 PRS-001 기준)
        self.user_title: str = os.getenv("EDDIE_USER_TITLE", "정혁님")

        # LLM 모델 (저비용 기본값)
        self.model: str = os.getenv("EDDIE_OPENAI_MODEL", "gpt-5.4-mini")

        # 도구 호출 루프 최대 반복 (무한루프 방지)
        self.max_tool_rounds: int = int(os.getenv("EDDIE_MAX_TOOL_ROUNDS", "5"))

        # 시스템 프롬프트 로드 (PRS-001 8장)
        self.system_prompt: str = self._load_system_prompt()

        # 대화 히스토리 (multi-turn context) — user/assistant 텍스트 턴만 보관.
        # 도구 호출 라운드는 chat() 1회 안에서만 유지되고 히스토리에는 남기지 않음.
        self.conversation_history: list[dict] = []

        # 지연 생성 자원
        self._client = None       # OpenAI 클라이언트
        self._web_search = None   # WebSearch 인스턴스 (MOD-TOL-001)

    def _load_system_prompt(self) -> str:
        """src/prompts/eddie_system_prompt.txt 에서 시스템 프롬프트 로드."""
        prompt_path = (
            Path(__file__).parent.parent / "prompts" / "eddie_system_prompt.txt"
        )
        if not prompt_path.exists():
            raise FileNotFoundError(
                f"시스템 프롬프트 파일을 찾을 수 없습니다: {prompt_path}"
            )
        return prompt_path.read_text(encoding="utf-8")

    # ============================================================
    # 도구 정의 (OpenAI function calling 스키마)
    # ============================================================
    def _tool_schemas(self) -> list[dict]:
        """모델에 노출할 도구 스키마 목록."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": (
                        "웹에서 최신 정보·뉴스를 검색한다. "
                        "학습 데이터에 없거나 최근에 바뀐 정보(시세, 뉴스, 최신 사실 등)가 "
                        "필요할 때 사용한다."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "검색어 (한국어 또는 영어)",
                            },
                            "max_results": {
                                "type": "integer",
                                "description": "가져올 결과 개수 (기본 5, 최대 20)",
                            },
                            "topic": {
                                "type": "string",
                                "enum": ["general", "news"],
                                "description": "검색 주제 분류. 시사·속보는 news.",
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
        ]

    def _get_web_search(self):
        """WebSearch(MOD-TOL-001) 지연 생성. src/ 를 경로에 추가 후 import."""
        if self._web_search is None:
            src_dir = Path(__file__).parent.parent  # .../src
            if str(src_dir) not in sys.path:
                sys.path.insert(0, str(src_dir))
            from action.web_search import WebSearch

            self._web_search = WebSearch()
        return self._web_search

    def _exec_tool(self, name: str, args: dict) -> str:
        """도구 1건 실행 → 결과를 JSON 문자열로 반환 (모델에 다시 주입할 형태).

        도구 자체 에러는 여기서 잡아 JSON으로 돌려준다 → 모델이 보고 복구 가능.
        """
        try:
            if name == "web_search":
                result = self._get_web_search().search(**args)
                return json.dumps(result, ensure_ascii=False)
            return json.dumps(
                {"status": "error", "message": f"알 수 없는 도구: {name}"},
                ensure_ascii=False,
            )
        except Exception as e:  # noqa: BLE001
            return json.dumps(
                {"status": "error", "message": f"{type(e).__name__}: {e}"},
                ensure_ascii=False,
            )

    def chat(self, user_message: str) -> str:
        """사용자 메시지에 대한 EDDIE 응답 생성."""
        self.conversation_history.append(
            {"role": "user", "content": user_message}
        )

        if self.mock_mode:
            response = self._mock_response(user_message)
        else:
            response = self._real_response(user_message)

        self.conversation_history.append(
            {"role": "assistant", "content": response}
        )
        return response

    def _mock_response(self, user_message: str) -> str:
        """MOCK 응답 생성 (규칙 기반, API 없음)."""
        msg = user_message.strip().lower()
        title = self.user_title

        if any(kw in msg for kw in ["안녕", "hi", "hello", "ㅎㅇ"]):
            return f"안녕하세요 {title}, 에디예요. 무엇을 도와드릴까요?"
        if any(kw in msg for kw in ["누구", "이름", "정체"]):
            return (
                f"저는 에디예요. "
                f"Eduino's Digital Development Intelligent Engineer의 약자이며, "
                f"{title}의 전담 AI 비서로 동작합니다."
            )
        if any(kw in msg for kw in ["뭐 해", "뭐해", "할 수", "할수", "능력", "기능"]):
            return (
                f"현재 개발 단계입니다. "
                f"본 단계 완료 후 웹 검색, 파일 조작, 브라우저 제어, 이메일 발송을 "
                f"음성 명령으로 수행할 예정입니다."
            )
        if any(kw in msg for kw in ["감사", "고마", "thank"]):
            return f"별말씀을요, {title}."
        if any(kw in msg for kw in ["검색", "찾아", "알려줘"]):
            return (
                f"검색 기능은 곧 연결될 예정입니다. "
                f"현재 단계에서는 검색을 실행할 수 없습니다."
            )
        if any(kw in msg for kw in ["파일", "폴더", "정리"]):
            return (
                f"파일 조작 기능은 곧 연결될 예정입니다. "
                f"현재 단계에서는 파일 시스템에 접근하지 않습니다."
            )
        preview = user_message[:40] + ("..." if len(user_message) > 40 else "")
        return (
            f"알겠습니다, {title}. "
            f'현재는 MOCK 모드라 실제 작업을 수행하지 않습니다. '
            f'입력하신 내용: "{preview}"'
        )

    def _get_client(self):
        """OpenAI 클라이언트 지연 생성. MOCK 모드에서는 호출되지 않음."""
        if self._client is None:
            if not os.getenv("OPENAI_API_KEY"):
                raise RuntimeError(
                    "OPENAI_API_KEY 가 설정되지 않았습니다. .env 파일을 확인하십시오."
                )
            from openai import OpenAI

            self._client = OpenAI()
        return self._client

    def _real_response(self, user_message: str) -> str:
        """실제 OpenAI API 호출 + 도구 호출 루프 (ReAct).

        루프: 모델 호출 → tool_calls 있으면 실행·결과 주입 후 재호출 →
              tool_calls 없으면 최종 텍스트 반환.
        """
        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt},
            *self.conversation_history,
        ]
        tools = self._tool_schemas()

        try:
            client = self._get_client()

            for _ in range(self.max_tool_rounds):
                completion = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                )
                msg = completion.choices[0].message

                # 도구 호출이 없으면 최종 응답
                if not msg.tool_calls:
                    content = msg.content
                    if not content or not content.strip():
                        return "(SYSTEM) 모델이 빈 응답을 반환했습니다. 로그를 확인하십시오."
                    return content.strip()

                # 도구 호출 라운드: 어시스턴트 메시지(도구 요청) 기록
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in msg.tool_calls
                        ],
                    }
                )

                # 각 도구 실행 후 결과를 tool 메시지로 주입
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = self._exec_tool(tc.function.name, args)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        }
                    )

            # 루프 한도 초과
            return (
                "(SYSTEM) 도구 호출이 너무 많이 반복되어 중단했습니다. "
                "질문을 더 단순하게 다시 시도해 주십시오."
            )

        except Exception as e:  # noqa: BLE001 — 음성/HUD가 죽지 않도록 에러를 문자열로 반환
            return f"(ERROR) OpenAI 호출 실패: {type(e).__name__}: {e}"

    # === 유틸리티 메서드 ===

    def clear_history(self) -> None:
        """대화 히스토리 초기화."""
        self.conversation_history = []

    def get_mode_label(self) -> str:
        """현재 모드 라벨."""
        return (
            "MOCK (가짜 응답, API 호출 없음)"
            if self.mock_mode
            else f"REAL (OpenAI {self.model})"
        )

    def get_turn_count(self) -> int:
        """현재 대화 턴 수 (user 발화 기준)."""
        return sum(1 for m in self.conversation_history if m["role"] == "user")