"""
MOD-LLM-001 — 추론 엔진 (Reasoning Engine)

기술 기반: Anthropic Claude API + Tool Use + MCP
의도: 사용자 의도 분석부터 응답 합성까지 단일 LLM이 일관되게 수행
입력: 사용자 발화 텍스트, 시스템 프롬프트, 대화 컨텍스트, 도구 실행 결과
출력: 도구 호출 명령 또는 최종 응답 텍스트
핵심 책임: Intent classification, Tool selection, Response synthesis, Multi-turn context 관리

Phase 1 구현 단계:
  Step 1-2 (현재): MOCK 모드 응답 생성기. API 결제 없이 챗 루프 검증.
  Step 1-3 (예정): 실제 Claude API 호출 추가.
  Step 1-4~6 (예정): Tool Use 통합.
"""

import os
from pathlib import Path
from typing import Optional


class EddieCore:
    """EDDIE의 추론 엔진.

    환경변수 EDDIE_MOCK_MODE 값에 따라 MOCK 또는 REAL 모드로 동작.
      true  → MOCK 모드 (가짜 응답, API 호출 없음)
      false → REAL 모드 (Claude API 호출)
    """

    def __init__(self) -> None:
        # 모드 결정
        mock_env = os.getenv("EDDIE_MOCK_MODE", "true").strip().lower()
        self.mock_mode: bool = mock_env in ("true", "1", "yes", "on")

        # 사용자 호칭 (페르소나 정의서 PRS-001 기준)
        self.user_title: str = os.getenv("EDDIE_USER_TITLE", "소장님")

        # 시스템 프롬프트 로드 (PRS-001 8장)
        self.system_prompt: str = self._load_system_prompt()

        # 대화 히스토리 (multi-turn context)
        self.conversation_history: list[dict] = []

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

    def chat(self, user_message: str) -> str:
        """사용자 메시지에 대한 EDDIE 응답 생성."""
        # 사용자 메시지를 히스토리에 추가
        self.conversation_history.append(
            {"role": "user", "content": user_message}
        )

        # 모드에 따라 응답 생성
        if self.mock_mode:
            response = self._mock_response(user_message)
        else:
            response = self._real_response(user_message)

        # EDDIE 응답을 히스토리에 추가
        self.conversation_history.append(
            {"role": "assistant", "content": response}
        )

        return response

    def _mock_response(self, user_message: str) -> str:
        """MOCK 응답 생성.

        실제 LLM 없이 페르소나 톤을 흉내내는 규칙 기반 응답.
        Step 1-3에서 실제 Claude API로 교체될 예정.
        """
        msg = user_message.strip().lower()
        title = self.user_title

        # 입력 키워드별 분기
        if any(kw in msg for kw in ["안녕", "hi", "hello", "ㅎㅇ"]):
            return f"(MOCK) 안녕하세요, {title}. EDDIE입니다. 무엇을 도와드릴까요?"

        if any(kw in msg for kw in ["누구", "이름", "정체"]):
            return (
                f"(MOCK) 저는 EDDIE입니다. "
                f"Eduino's Digital Development Intelligent Engineer의 약자이며, "
                f"{title}의 전담 AI 비서로 동작합니다."
            )

        if any(kw in msg for kw in ["뭐 해", "뭐해", "할 수", "할수", "능력", "기능"]):
            return (
                f"(MOCK) 현재 Phase 1 개발 단계입니다. "
                f"본 단계 완료 후 웹 검색, 파일 조작, 브라우저 제어, 이메일 발송을 "
                f"음성 명령으로 수행할 예정입니다."
            )

        if any(kw in msg for kw in ["감사", "고마", "thank"]):
            return f"(MOCK) 별말씀을요, {title}."

        if any(kw in msg for kw in ["검색", "찾아", "알려줘"]):
            return (
                f"(MOCK) 검색 도구는 Step 1-5에서 구현 예정입니다. "
                f"현재 단계에서는 검색을 실행할 수 없습니다."
            )

        if any(kw in msg for kw in ["파일", "폴더", "정리"]):
            return (
                f"(MOCK) 파일 조작 도구는 Step 1-4에서 구현 예정입니다. "
                f"현재 단계에서는 파일 시스템에 접근하지 않습니다."
            )

        # 기본 응답 (입력 일부 에코)
        preview = user_message[:40] + ("..." if len(user_message) > 40 else "")
        return (
            f"(MOCK) 알겠습니다, {title}. "
            f'현재는 MOCK 모드라 실제 작업을 수행하지 않습니다. '
            f'입력하신 내용: "{preview}"'
        )

    def _real_response(self, user_message: str) -> str:
        """실제 Claude API 호출. Step 1-3에서 구현 예정."""
        return (
            "(SYSTEM) REAL 모드는 Step 1-3에서 구현될 예정입니다. "
            ".env 의 EDDIE_MOCK_MODE 를 true 로 되돌려주십시오."
        )

    # === 유틸리티 메서드 ===

    def clear_history(self) -> None:
        """대화 히스토리 초기화."""
        self.conversation_history = []

    def get_mode_label(self) -> str:
        """현재 모드 라벨."""
        return "MOCK (가짜 응답, API 호출 없음)" if self.mock_mode else "REAL (Claude API)"

    def get_turn_count(self) -> int:
        """현재 대화 턴 수 (user 발화 기준)."""
        return sum(1 for m in self.conversation_history if m["role"] == "user")
