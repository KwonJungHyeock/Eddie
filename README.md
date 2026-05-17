# EDDIE — Eduino's Digital Development Intelligent Engineer

에듀이노 기업부설연구소의 음성 기반 개인 AI 비서 프로젝트.

## 프로젝트 정보

| 항목 | 내용 |
|---|---|
| **명칭** | EDDIE |
| **풀네임** | Eduino's Digital Development Intelligent Engineer |
| **유형** | LLM 기반 음성 대화형 자율 에이전트 (Single-tenant) |
| **운영 환경** | Windows 11, Python 3.12+, Node.js LTS |
| **현재 단계** | Phase 0 — 환경 셋업 완료 |

## 기획 문서

`docs/` 폴더 참조 (Word + PNG 양식):

- **EDDIE-ARC-001** — 시스템 아키텍처 정의서
- **EDDIE-PRS-001** — 페르소나 정의서 (시스템 프롬프트 포함)
- **EDDIE-PLN-001** — 프로젝트 기획서

## 폴더 구조

```
Eddie/
├── docs/             # 기획 문서 3종 (ARC, PRS, PLN)
├── src/
│   ├── core/         # MOD-LLM-001 — Claude API 추론 엔진
│   ├── perception/   # MOD-IN-001, MOD-STT-001 — 음성 입력
│   ├── action/       # MOD-TOL-001~004 — 도구 4종
│   ├── output/       # MOD-TTS-001, MOD-OUT-001 — 출력
│   └── prompts/      # 시스템 프롬프트 텍스트 (PRS-001 8장)
├── tests/            # 테스트
├── config/           # 설정 파일
├── logs/             # 로그 (gitignored)
├── .env.example      # 환경변수 템플릿
├── .gitignore        # API 키 보호 등
├── requirements.txt  # Python 의존성
├── package.json      # Node.js 의존성 (Phase 3+)
└── main.py           # 진입점
```

## 빠른 시작 (Phase 0 검증)

```cmd
:: 1. 가상환경 생성
python -m venv venv
venv\Scripts\activate

:: 2. 의존성 설치 (Phase 0~1 분량)
pip install -r requirements.txt

:: 3. 환경변수 파일 생성
copy .env.example .env
:: .env 를 메모장으로 열어서 필요한 값 입력 (MOCK 모드면 비워두고 진행)

:: 4. Phase 0 검증
python main.py
```

`Phase 0 환경 셋업 검증 완료` 가 출력되면 OK.

## Phase 로드맵

| Phase | 내용 | 기간 |
|---|---|---|
| 0 | 환경 셋업 & 기초 문서화 | ✓ 완료 |
| 1 | 텍스트 챗 + Tool Use MVP | 1~2주 |
| 2 | 음성 입출력 통합 | 1~2주 |
| 3 | 기본 GUI (Electron) | 1~2주 |
| 4 | 자비스 스타일 HUD | 2~4주 |
| 5 | 통합 자동화 (브라우저 + 이메일) | 1~2주 |

## 작성·운영

- **작성**: 에듀이노 기업부설연구소장
- **개발 시작**: 2026-05-16
- **개발 형태**: 단독 수행
