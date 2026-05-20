"""
MOD-TOL-001 — 웹 검색 도구

기술 기반: Tavily Search API (LLM 친화적 검색 엔진)
의도: LLM 학습 데이터에 없는 최신 정보 검색. 결과의 LLM 친화성과 안정성 우선.
입력: 검색 쿼리 (string)
출력: dict (status, query, count, results)
핵심 책임: API 호출, 결과 정제, 에러 처리, rate limit 인지

Phase 1 Step 1-5 구현. Step 1-7에서 EDDIE Tool Use와 통합 예정.

API 정책 (2026-05 검증):
  - Tavily Researcher (Free) 플랜: 월 1,000 credits, 카드 등록 불요
  - Pay-as-you-go 토글 기본 OFF: 한도 초과 시 검색 멈춤 (자동 과금 없음)
"""

from __future__ import annotations

import os
from typing import Optional

import httpx


class WebSearchError(Exception):
    """웹 검색 도구 보안/검증 실패."""


class WebSearch:
    """Tavily Search API 기반 웹 검색 도구."""

    API_URL = "https://api.tavily.com/search"
    TIMEOUT_SECONDS = 15.0
    SNIPPET_MAX_CHARS = 280  # 표시용 (저장 데이터는 원본 유지)

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = (api_key or os.getenv("TAVILY_API_KEY", "")).strip()
        if not self.api_key:
            raise WebSearchError(
                "TAVILY_API_KEY가 설정되지 않았습니다. .env 파일에 TAVILY_API_KEY=tvly-... 추가 필요."
            )
        if not self.api_key.startswith("tvly-"):
            raise WebSearchError(
                "TAVILY_API_KEY 형식이 잘못되었습니다. 'tvly-' 로 시작해야 합니다."
            )

    def search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "basic",
        topic: str = "general",
    ) -> dict:
        """웹 검색 실행."""
        query = (query or "").strip()
        if not query:
            return {"status": "error", "message": "빈 쿼리 거부"}

        if search_depth not in ("basic", "advanced"):
            search_depth = "basic"
        if topic not in ("general", "news"):
            topic = "general"
        max_results = max(1, min(int(max_results), 20))

        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": search_depth,
            "max_results": max_results,
            "topic": topic,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }

        try:
            resp = httpx.post(self.API_URL, json=payload, timeout=self.TIMEOUT_SECONDS)
        except httpx.TimeoutException:
            return {"status": "error", "message": "Tavily 응답 시간 초과"}
        except httpx.RequestError as e:
            return {"status": "error", "message": f"네트워크 에러: {e}"}

        # 상태 코드별 명확한 에러 메시지
        if resp.status_code == 401:
            return {"status": "error", "message": "API 키 인증 실패. .env 의 TAVILY_API_KEY 재확인 필요."}
        if resp.status_code == 429:
            return {"status": "error", "message": "Tavily 한도 초과. 무료 1,000 credits 소진 가능성. 다음 달 리셋 대기."}
        if resp.status_code != 200:
            body = resp.text[:200]
            return {"status": "error", "message": f"Tavily API 에러 (HTTP {resp.status_code}): {body}"}

        try:
            data = resp.json()
        except Exception as e:
            return {"status": "error", "message": f"응답 JSON 파싱 실패: {e}"}

        results = []
        for item in data.get("results", []):
            results.append({
                "title": item.get("title", "").strip(),
                "url": item.get("url", "").strip(),
                "snippet": item.get("content", "").strip(),
                "score": float(item.get("score", 0.0)),
            })

        return {
            "status": "ok",
            "query": query,
            "search_depth": search_depth,
            "topic": topic,
            "count": len(results),
            "results": results,
        }


# ===========================================================================
# 데모: python -m src.action.web_search [검색어...]
# ===========================================================================

def _demo():
    """웹 검색 도구의 3가지 시나리오를 자동 시연한다."""
    import sys
    from pathlib import Path

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    # .env 로드 (Eddie/ 루트의 .env)
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except ImportError:
        pass

    LINE = "=" * 68

    def header(num: int, title: str) -> None:
        print()
        print(LINE)
        print(f"  [{num}] {title}")
        print(LINE)

    def show_results(result: dict) -> None:
        if result["status"] != "ok":
            print(f"    [에러] {result['message']}")
            return
        print(f"  쿼리: {result['query']}")
        print(f"  결과 {result['count']}건 (depth={result['search_depth']}, topic={result['topic']}):")
        print()
        for i, r in enumerate(result["results"], 1):
            print(f"  [{i}] {r['title']}")
            print(f"      URL: {r['url']}")
            sn = r["snippet"]
            if len(sn) > WebSearch.SNIPPET_MAX_CHARS:
                sn = sn[:WebSearch.SNIPPET_MAX_CHARS] + "..."
            print(f"      {sn}")
            print(f"      score: {r['score']:.3f}")
            print()

    print()
    print(LINE)
    print("  MOD-TOL-001 웹 검색 도구 데모 (Tavily Search API)")
    print(LINE)

    # 초기화
    try:
        ws = WebSearch()
    except WebSearchError as e:
        print(f"  [초기화 실패] {e}")
        return

    # 사용자 인자 또는 기본 쿼리
    if len(sys.argv) > 1:
        user_query = " ".join(sys.argv[1:])
        header(1, f"사용자 쿼리: {user_query}")
        show_results(ws.search(user_query, max_results=5))
        print(LINE)
        print("  데모 완료")
        print(LINE)
        return

    # 기본 3개 시나리오
    header(1, "기본 검색 (general)")
    show_results(ws.search("Anthropic Claude Sonnet API documentation"))

    header(2, "한국어 검색")
    show_results(ws.search("아이언맨 자비스 AI 비서"))

    header(3, "에러 시연 — 빈 쿼리")
    print(f"  결과: {ws.search('')}")

    print()
    print(LINE)
    print("  데모 완료")
    print(LINE)


if __name__ == "__main__":
    _demo()