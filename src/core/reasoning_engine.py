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
  Step 2a (완료): 웹 검색 도구(MOD-TOL-001).
  Step 2b (완료): 파일 조작 도구(MOD-TOL-002).
  Step 2c (현재): 브라우저 제어 도구(MOD-TOL-003). Phase 1 도구 통합 완료.

자원 정리: 브라우저는 세션 내내 1개 인스턴스를 재사용한다.
           앱 종료 시 반드시 EddieCore.close() 를 호출할 것.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path


class EddieCore:
    """EDDIE의 추론 엔진.

    환경변수 EDDIE_MOCK_MODE 값에 따라 MOCK 또는 REAL 모드로 동작.
      true  → MOCK 모드 (가짜 응답, API 호출 없음)
      false → REAL 모드 (OpenAI API 호출)
    """

    # 파일 도구 이름 → FileOperations 메서드 (delete는 별도 처리)
    _FILE_METHOD_MAP = {
        "file_list": "list_dir",
        "file_read": "read_file",
        "file_info": "info",
        "file_find": "find",
        "file_mkdir": "mkdir",
        "file_copy": "copy",
        "file_move": "move",
    }
    # 브라우저 도구 이름 → BrowserControl 메서드 (screenshot은 별도 처리)
    _BROWSER_METHOD_MAP = {
        "browser_navigate": "navigate",
        "browser_get_title": "get_title",
        "browser_get_text": "get_text",
        "browser_extract_links": "extract_links",
    }

    def __init__(self) -> None:
        # .env 로드 (어떤 런처로 띄워도 환경변수가 보장되도록 가장 먼저 수행)
        self._load_env()

        # 모드 결정
        mock_env = os.getenv("EDDIE_MOCK_MODE", "true").strip().lower()
        self.mock_mode: bool = mock_env in ("true", "1", "yes", "on")

        # 사용자 호칭 (페르소나 정의서 PRS-001 기준)
        self.user_title: str = os.getenv("EDDIE_USER_TITLE", "정혁님")

        # LLM 모델 (저비용 기본값)
        self.model: str = os.getenv("EDDIE_OPENAI_MODEL", "gpt-5.4-mini")

        # 도구 호출 루프 최대 반복 (무한루프 방지)
        self.max_tool_rounds: int = int(os.getenv("EDDIE_MAX_TOOL_ROUNDS", "8"))

        # 파일 접근 허용 루트 (비우면 시스템 폴더 외 전체 허용)
        self.file_root: str | None = os.getenv("EDDIE_FILE_ROOT") or None

        # 브라우저 설정
        headless_env = os.getenv("EDDIE_BROWSER_HEADLESS", "true").strip().lower()
        self.browser_headless: bool = headless_env in ("true", "1", "yes", "on")
        domains_env = os.getenv("EDDIE_BROWSER_DOMAINS", "").strip()
        self.browser_domains: list[str] | None = (
            [d.strip() for d in domains_env.split(",") if d.strip()]
            if domains_env
            else None
        )

        # 시스템 프롬프트 로드 (PRS-001 8장)
        self.system_prompt: str = self._load_system_prompt()

        # 대화 히스토리 (multi-turn context) — user/assistant 텍스트 턴만 보관.
        self.conversation_history: list[dict] = []

        # 지연 생성 자원
        self._client = None       # OpenAI 클라이언트
        self._web_search = None   # WebSearch (MOD-TOL-001)
        self._file_ops = None     # FileOperations (MOD-TOL-002)
        self._browser = None      # BrowserControl (MOD-TOL-003), 세션 재사용
        self._arduino = None      # ArduinoControl (MOD-TOL-005)

    @staticmethod
    def _load_env() -> None:
        """프로젝트 루트의 .env 를 로드한다.

        실행 위치(cwd)에 의존하지 않도록 이 파일 기준 절대경로(<프로젝트>/.env)를 지정.
        이미 설정된 환경변수는 덮어쓰지 않는다(override=False) → 셸/테스트 강제값 우선.
        python-dotenv 미설치 시 조용히 통과(이미 환경변수가 잡혀 있을 수 있음).
        """
        try:
            from dotenv import load_dotenv

            env_path = Path(__file__).resolve().parent.parent.parent / ".env"
            load_dotenv(env_path)
        except Exception:  # noqa: BLE001
            pass

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
            # --- MOD-TOL-001 웹 검색 ---
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": (
                        "웹에서 최신 정보·뉴스를 검색한다. 학습 데이터에 없거나 "
                        "최근에 바뀐 정보가 필요할 때 사용한다."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "검색어"},
                            "max_results": {"type": "integer", "description": "결과 개수 (기본 5)"},
                            "topic": {
                                "type": "string",
                                "enum": ["general", "news"],
                                "description": "검색 주제. 시사·속보는 news.",
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
            # --- MOD-TOL-002 파일 조작 ---
            {
                "type": "function",
                "function": {
                    "name": "file_list",
                    "description": "디렉토리의 파일·폴더 목록을 조회한다.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "조회할 폴더 경로"},
                            "pattern": {"type": "string", "description": "glob 패턴 (기본 '*')"},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "file_read",
                    "description": "텍스트 파일 내용을 읽는다 (인코딩 자동 감지, 최대 10MB).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "읽을 파일 경로"},
                            "max_chars": {"type": "integer", "description": "최대 글자 수"},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "file_info",
                    "description": "파일/폴더의 메타데이터(크기, 시각 등)를 반환한다.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "대상 경로"},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "file_find",
                    "description": "지정 폴더에서 패턴으로 파일을 검색한다.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "root": {"type": "string", "description": "검색 시작 폴더"},
                            "pattern": {"type": "string", "description": "glob 패턴 (예: '*.md')"},
                            "recursive": {"type": "boolean", "description": "하위 폴더까지 (기본 true)"},
                            "max_results": {"type": "integer", "description": "최대 결과 수 (기본 100)"},
                        },
                        "required": ["root", "pattern"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "file_mkdir",
                    "description": "디렉토리를 생성한다 (이미 있으면 무시).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "생성할 폴더 경로"},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "file_copy",
                    "description": "파일이나 폴더를 복사한다.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "src": {"type": "string", "description": "원본 경로"},
                            "dst": {"type": "string", "description": "복사 대상 경로"},
                        },
                        "required": ["src", "dst"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "file_move",
                    "description": "파일이나 폴더를 이동(또는 이름 변경)한다.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "src": {"type": "string", "description": "원본 경로"},
                            "dst": {"type": "string", "description": "이동 대상 경로"},
                        },
                        "required": ["src", "dst"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "file_delete",
                    "description": (
                        "파일이나 폴더를 삭제한다. 항상 휴지통으로 이동하므로 복구 가능. "
                        "영구 삭제는 지원하지 않는다."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "삭제할 경로"},
                        },
                        "required": ["path"],
                    },
                },
            },
            # --- MOD-TOL-003 브라우저 제어 (file://·javascript: 차단) ---
            {
                "type": "function",
                "function": {
                    "name": "browser_navigate",
                    "description": (
                        "브라우저로 URL에 접속한다. 검색만으로 부족하고 실제 페이지를 "
                        "열어 내용을 봐야 할 때 먼저 호출한다."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "접속할 http/https URL"},
                            "wait_until": {
                                "type": "string",
                                "enum": ["load", "domcontentloaded", "networkidle"],
                                "description": "로딩 완료 기준 (기본 domcontentloaded)",
                            },
                        },
                        "required": ["url"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "browser_get_title",
                    "description": "현재 열린 페이지의 제목을 가져온다.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "browser_get_text",
                    "description": "현재 페이지의 텍스트를 추출한다. navigate 이후 사용.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "selector": {
                                "type": "string",
                                "description": "CSS 셀렉터 (없으면 body 전체)",
                            },
                            "max_chars": {"type": "integer", "description": "최대 글자 수 (기본 2000)"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "browser_extract_links",
                    "description": "현재 페이지의 링크 목록을 추출한다.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "max_results": {"type": "integer", "description": "최대 링크 수 (기본 20)"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "browser_screenshot",
                    "description": (
                        "현재 페이지의 스크린샷을 저장한다. 저장 경로(파일)를 반환한다."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "full_page": {
                                "type": "boolean",
                                "description": "전체 페이지 캡처 여부 (기본 false)",
                            },
                        },
                    },
                },
            },
            # --- MOD-TOL-005 아두이노 제어 ---
            {
                "type": "function",
                "function": {
                    "name": "arduino_list_boards",
                    "description": "USB로 연결된 아두이노 보드와 포트를 조회한다.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "arduino_write_sketch",
                    "description": (
                        "아두이노 스케치(.ino) 코드를 저장한다. 코드를 새로 작성하거나 "
                        "수정할 때 호출한다. 같은 name으로 다시 호출하면 덮어쓴다."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "스케치 이름 (영문/숫자 권장, 예: blink)",
                            },
                            "code": {
                                "type": "string",
                                "description": "아두이노 C++ 스케치 전체 코드 (setup·loop 포함)",
                            },
                        },
                        "required": ["name", "code"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "arduino_compile",
                    "description": (
                        "저장된 스케치를 컴파일한다. 보드는 자동 설정된다. "
                        "실패하면 compiler_error를 읽고 코드를 고친 뒤 arduino_write_sketch로 "
                        "다시 저장하고 재컴파일한다. 성공 후에 업로드한다."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "컴파일할 스케치 이름"},
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "arduino_upload",
                    "description": (
                        "컴파일이 성공한 스케치를 연결된 보드에 업로드한다. "
                        "포트와 보드는 자동 감지된다. 컴파일 성공을 먼저 확인한 뒤 호출한다."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "업로드할 스케치 이름"},
                        },
                        "required": ["name"],
                    },
                },
            },
        ]

    # ============================================================
    # 도구 인스턴스 (지연 생성)
    # ============================================================
    def _ensure_src_on_path(self) -> None:
        """src/ 를 import 경로에 추가 (action.* 모듈 import용)."""
        src_dir = Path(__file__).parent.parent  # .../src
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))

    def _get_web_search(self):
        if self._web_search is None:
            self._ensure_src_on_path()
            from action.web_search import WebSearch

            self._web_search = WebSearch()
        return self._web_search

    def _get_file_ops(self):
        if self._file_ops is None:
            self._ensure_src_on_path()
            from action.file_ops import FileOperations

            self._file_ops = FileOperations(allowed_root=self.file_root)
        return self._file_ops

    def _get_browser(self):
        """BrowserControl(MOD-TOL-003) 지연 생성 + 브라우저 시작.

        컨텍스트 매니저를 수동으로 진입(__enter__)하고 세션 내내 재사용한다.
        """
        if self._browser is None:
            self._ensure_src_on_path()
            from action.browser_control import BrowserControl

            bc = BrowserControl(
                headless=self.browser_headless,
                allowed_domains=self.browser_domains,
            )
            bc.__enter__()  # 브라우저 프로세스 시작
            self._browser = bc
        return self._browser

    def _get_arduino(self):
        """ArduinoControl(MOD-TOL-005) 지연 생성."""
        if self._arduino is None:
            self._ensure_src_on_path()
            from action.arduino_control import ArduinoControl

            self._arduino = ArduinoControl()
        return self._arduino

    def _auto_screenshot_path(self) -> str:
        """스크린샷 자동 저장 경로 (<프로젝트>/screenshots/shot_타임스탬프.png)."""
        project_root = Path(__file__).resolve().parent.parent.parent
        shot_dir = project_root / "screenshots"
        shot_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return str(shot_dir / f"shot_{stamp}.png")

    def _exec_tool(self, name: str, args: dict) -> str:
        """도구 1건 실행 → 결과를 JSON 문자열로 반환 (모델에 다시 주입)."""
        try:
            if name == "web_search":
                return json.dumps(
                    self._get_web_search().search(**args), ensure_ascii=False
                )

            if name in self._FILE_METHOD_MAP:
                method = getattr(self._get_file_ops(), self._FILE_METHOD_MAP[name])
                return json.dumps(method(**args), ensure_ascii=False)

            if name == "file_delete":
                # to_trash는 모델에 노출하지 않고 항상 휴지통 강제 (안전 정책)
                result = self._get_file_ops().delete(
                    path=args.get("path"), to_trash=True
                )
                return json.dumps(result, ensure_ascii=False)

            if name in self._BROWSER_METHOD_MAP:
                method = getattr(self._get_browser(), self._BROWSER_METHOD_MAP[name])
                return json.dumps(method(**args), ensure_ascii=False)

            if name == "browser_screenshot":
                path = args.get("path") or self._auto_screenshot_path()
                result = self._get_browser().screenshot(
                    path=path, full_page=args.get("full_page", False)
                )
                return json.dumps(result, ensure_ascii=False)

            if name == "arduino_list_boards":
                return json.dumps(
                    self._get_arduino().list_boards(), ensure_ascii=False
                )
            if name == "arduino_write_sketch":
                return json.dumps(
                    self._get_arduino().write_sketch(
                        name=args.get("name"), code=args.get("code", "")
                    ),
                    ensure_ascii=False,
                )
            if name == "arduino_compile":
                return json.dumps(
                    self._get_arduino().compile(name=args.get("name")),
                    ensure_ascii=False,
                )
            if name == "arduino_upload":
                return json.dumps(
                    self._get_arduino().upload(name=args.get("name")),
                    ensure_ascii=False,
                )

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
        """실제 OpenAI API 호출 + 도구 호출 루프 (ReAct)."""
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

                if not msg.tool_calls:
                    content = msg.content
                    if not content or not content.strip():
                        return "(SYSTEM) 모델이 빈 응답을 반환했습니다. 로그를 확인하십시오."
                    return content.strip()

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

            return (
                "(SYSTEM) 도구 호출이 너무 많이 반복되어 중단했습니다. "
                "질문을 더 단순하게 다시 시도해 주십시오."
            )

        except Exception as e:  # noqa: BLE001
            return f"(ERROR) OpenAI 호출 실패: {type(e).__name__}: {e}"

    # === 자원 정리 ===

    def close(self) -> None:
        """세션 종료 시 자원 정리. 앱 종료부(voice_chat.py)에서 반드시 호출."""
        if self._browser is not None:
            try:
                self._browser.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._browser = None

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
