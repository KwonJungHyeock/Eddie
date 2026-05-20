"""
MOD-TOL-003 — 브라우저 제어 도구

기술 기반: Microsoft Playwright (Python sync API) + Chromium
의도: 헤드리스/헤디드 브라우저 인스턴스 제어. 검색 결과만으로 부족한 경우
      실제 페이지를 자동 탐색·데이터 추출. Tavily(검색)와 보완 관계.
입력: URL, 액션 명령 (navigate / get_text / get_title / screenshot / extract_links)
출력: dict (status, url, title, content 등)
핵심 책임: 브라우저 자동화, 동적 콘텐츠 렌더링 대기, URL 안전 검증, 에러 복원

Phase 1 Step 1-6 구현. Step 1-7 에서 EDDIE Tool Use 와 통합 예정.

핵심 안전 정책:
  1. http:// https:// 만 허용 (file://, about:, chrome:// 등 차단)
  2. 도메인 화이트리스트 옵션 (allowed_domains)
  3. 페이지 로드 타임아웃 30초 상한
  4. JavaScript 실행은 페이지 자체에서만 (eval/exec 등 임의 코드 실행 금지)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


class BrowserControlError(Exception):
    """브라우저 제어 도구 보안/검증 실패."""


class BrowserControl:
    """Playwright Chromium 기반 브라우저 제어 도구.

    컨텍스트 매니저로 사용한다:
        with BrowserControl(headless=True) as bc:
            result = bc.navigate("https://example.com")
            title = bc.get_title()
    """

    ALLOWED_SCHEMES = ("http", "https")
    DEFAULT_TIMEOUT_MS = 30_000          # 30s
    DEFAULT_VIEWPORT = {"width": 1280, "height": 800}
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36 EDDIE/0.1"
    )

    def __init__(
        self,
        headless: bool = True,
        allowed_domains: Optional[list[str]] = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> None:
        self.headless = headless
        self.allowed_domains = allowed_domains
        self.timeout_ms = timeout_ms
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    # === 컨텍스트 매니저 ===

    def __enter__(self) -> "BrowserControl":
        # lazy import: Playwright 미설치 환경에서도 import 자체는 가능하도록
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            viewport=self.DEFAULT_VIEWPORT,
            user_agent=self.USER_AGENT,
        )
        self._page = self._context.new_page()
        self._page.set_default_timeout(self.timeout_ms)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

    # === 내부 유틸 ===

    def _validate_url(self, url: str) -> str:
        """URL 안전성 검증. 통과하면 URL 반환, 실패하면 예외."""
        url = (url or "").strip()
        if not url:
            raise BrowserControlError("빈 URL 거부")

        try:
            parsed = urlparse(url)
        except Exception as e:
            raise BrowserControlError(f"URL 파싱 실패: {e}") from e

        if parsed.scheme not in self.ALLOWED_SCHEMES:
            raise BrowserControlError(
                f"허용되지 않은 스키마: {parsed.scheme}:// "
                f"(허용: http, https)"
            )

        if not parsed.netloc:
            raise BrowserControlError("호스트 누락 URL 거부")

        if self.allowed_domains is not None:
            host = parsed.netloc.lower()
            if not any(host.endswith(d.lower()) for d in self.allowed_domains):
                raise BrowserControlError(
                    f"허용 도메인 외부 접근 차단: {host} "
                    f"(허용: {self.allowed_domains})"
                )

        return url

    def _ensure_page(self):
        if self._page is None:
            raise BrowserControlError("브라우저 초기화 안 됨. with 문 사용 필요.")
        return self._page

    # === 공개 메서드 ===

    def navigate(self, url: str, wait_until: str = "domcontentloaded") -> dict:
        """URL 로 이동한다."""
        try:
            url = self._validate_url(url)
        except BrowserControlError as e:
            return {"status": "blocked", "message": str(e)}

        if wait_until not in ("load", "domcontentloaded", "networkidle"):
            wait_until = "domcontentloaded"

        page = self._ensure_page()
        try:
            response = page.goto(url, wait_until=wait_until)
        except Exception as e:
            return {"status": "error", "message": f"페이지 로드 실패: {e}"}

        return {
            "status": "ok",
            "url": page.url,
            "http_status": response.status if response else None,
            "final_url": page.url,
        }

    def get_title(self) -> dict:
        """현재 페이지의 제목."""
        page = self._ensure_page()
        try:
            return {"status": "ok", "url": page.url, "title": page.title()}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_text(self, selector: Optional[str] = None, max_chars: int = 2000) -> dict:
        """페이지 텍스트 추출. selector 지정 시 해당 요소만, 없으면 body."""
        page = self._ensure_page()
        try:
            if selector:
                elem = page.query_selector(selector)
                if not elem:
                    return {"status": "error", "message": f"셀렉터 매칭 없음: {selector}"}
                text = elem.inner_text()
            else:
                text = page.locator("body").inner_text()
        except Exception as e:
            return {"status": "error", "message": str(e)}

        text = (text or "").strip()
        truncated = False
        if len(text) > max_chars:
            text = text[:max_chars]
            truncated = True

        return {
            "status": "ok",
            "url": page.url,
            "selector": selector,
            "length": len(text),
            "truncated": truncated,
            "text": text,
        }

    def extract_links(self, max_results: int = 20) -> dict:
        """페이지의 링크 (href) 들을 추출한다."""
        page = self._ensure_page()
        try:
            links = page.eval_on_selector_all(
                "a[href]",
                "elems => elems.map(e => ({text: e.innerText.trim(), href: e.href}))"
            )
        except Exception as e:
            return {"status": "error", "message": str(e)}

        # 빈 텍스트 제거, 중복 href 제거
        seen = set()
        cleaned = []
        for link in links:
            href = (link.get("href") or "").strip()
            text = (link.get("text") or "").strip()
            if not href or href in seen:
                continue
            seen.add(href)
            cleaned.append({"text": text[:80], "href": href})
            if len(cleaned) >= max_results:
                break

        return {
            "status": "ok",
            "url": page.url,
            "count": len(cleaned),
            "links": cleaned,
        }

    def screenshot(self, path: str, full_page: bool = False) -> dict:
        """현재 페이지 스크린샷."""
        page = self._ensure_page()
        try:
            target = Path(path).expanduser().resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(target), full_page=full_page)
        except Exception as e:
            return {"status": "error", "message": str(e)}

        return {
            "status": "ok",
            "url": page.url,
            "path": str(target),
            "size": target.stat().st_size if target.exists() else 0,
            "full_page": full_page,
        }


# ===========================================================================
# 데모: python -m src.action.browser_control
# ===========================================================================

def _demo():
    """브라우저 제어 도구의 4가지 시나리오를 자동 시연한다."""
    import sys

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    LINE = "=" * 68

    def header(num: int, title: str) -> None:
        print()
        print(LINE)
        print(f"  [{num}] {title}")
        print(LINE)

    def show(result: dict, preview_text: bool = False) -> None:
        for k, v in result.items():
            if k == "text" and preview_text:
                val = str(v).replace("\n", " ")
                if len(val) > 300:
                    val = val[:300] + "..."
                print(f"  {k}: {val}")
            elif isinstance(v, list) and v and isinstance(v[0], dict):
                print(f"  {k}:")
                for item in v[:8]:
                    print(f"    - {item}")
                if len(v) > 8:
                    print(f"    ... ({len(v) - 8}건 더)")
            else:
                val = str(v)
                if len(val) > 200:
                    val = val[:200] + "..."
                print(f"  {k}: {val}")

    print()
    print(LINE)
    print("  MOD-TOL-003 브라우저 제어 도구 데모 (Playwright + Chromium)")
    print(LINE)

    with BrowserControl(headless=True) as bc:
        # 1. 페이지 이동
        target_url = "https://example.com"
        header(1, f"페이지 이동 — {target_url}")
        show(bc.navigate(target_url))

        # 2. 제목 추출
        header(2, "페이지 제목 추출")
        show(bc.get_title())

        # 3. 본문 텍스트 추출
        header(3, "본문 텍스트 추출 (body)")
        show(bc.get_text(max_chars=500), preview_text=True)

        # 4. 링크 추출
        header(4, "링크 추출")
        show(bc.extract_links(max_results=10))

        # 5. 스크린샷
        import tempfile
        screenshot_path = str(Path(tempfile.gettempdir()) / "eddie_demo_screenshot.png")
        header(5, f"스크린샷 — {screenshot_path}")
        show(bc.screenshot(screenshot_path))

        # 6. 안전 가드 시연 — file://
        header(6, "안전 가드 시연 — file:// 접근 차단")
        show(bc.navigate("file:///C:/Windows/System32/drivers/etc/hosts"))

        # 7. 안전 가드 시연 — 빈 URL
        header(7, "안전 가드 시연 — 빈 URL 거부")
        show(bc.navigate(""))

        # 8. 안전 가드 시연 — javascript: 스킴 차단
        header(8, "안전 가드 시연 — javascript: 스킴 차단")
        show(bc.navigate("javascript:alert('xss')"))

    # 9. 도메인 화이트리스트 검증 (브라우저 띄우지 않고 URL 검증만)
    header(9, "도메인 화이트리스트 검증")
    bc_restricted = BrowserControl(allowed_domains=["example.com", "anthropic.com"])
    test_urls = [
        "https://example.com/page1",
        "https://www.anthropic.com",
        "https://www.google.com",
        "https://malicious-site.example",
    ]
    print("  허용 도메인: ['example.com', 'anthropic.com']")
    print()
    for u in test_urls:
        try:
            bc_restricted._validate_url(u)
            print(f"    [통과]  {u}")
        except BrowserControlError as e:
            print(f"    [차단]  {u} -> {e}")

    print()
    print(LINE)
    print("  데모 완료")
    print(LINE)


if __name__ == "__main__":
    _demo()