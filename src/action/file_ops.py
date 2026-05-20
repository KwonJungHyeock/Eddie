"""
MOD-TOL-002 — 파일 조작 도구

기술 기반: Python pathlib, shutil, os 표준 라이브러리 + send2trash
의도: 사용자 PC 파일 시스템을 안전하게 탐색, 읽기, 이동, 정리.
      시스템 폴더 접근은 자동 차단하여 운영체제 보호.
입력: 파일 경로, 작업 명령 (list / read / info / find / mkdir / copy / move / delete)
출력: dict (status, path, content 등)
핵심 책임: 경로 검증, 시스템 폴더 차단, 인코딩 자동 감지, 휴지통 우선 삭제

Phase 1 Step 1-4 구현. Step 1-7 에서 EDDIE Tool Use 와 통합 예정.
"""

from __future__ import annotations

import platform
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from send2trash import send2trash
    _TRASH_AVAILABLE = True
except ImportError:
    _TRASH_AVAILABLE = False


class FileOperationError(Exception):
    """파일 조작 보안/검증 실패."""


class FileOperations:
    """안전 가드가 적용된 파일 시스템 조작 도구.

    핵심 안전 정책:
      1. 시스템 디렉토리 (Windows·Linux·Mac) 접근 차단
      2. allowed_root 지정 시 그 외부 접근 차단
      3. 큰 파일 (>10MB) 읽기 차단
      4. 삭제는 기본적으로 휴지통으로 (영구 삭제는 명시 요청)
      5. 인코딩 실패 시 자동 폴백 (UTF-8 → cp949 → latin-1)
    """

    BLOCKED_DIRS_WIN = (
        r"c:\windows",
        r"c:\program files",
        r"c:\program files (x86)",
        r"c:\programdata",
        r"c:\$recycle.bin",
        r"c:\system volume information",
    )
    BLOCKED_DIRS_UNIX = (
        "/etc", "/usr", "/bin", "/sbin", "/var/log", "/system", "/boot",
    )

    MAX_READ_BYTES = 10 * 1024 * 1024     # 10 MB
    MAX_LIST_ITEMS = 1000                  # 디렉토리 항목 상한
    READ_ENCODINGS = ("utf-8", "cp949", "latin-1")

    def __init__(self, allowed_root: Optional[str] = None) -> None:
        self.is_windows = platform.system() == "Windows"
        self.allowed_root = Path(allowed_root).expanduser().resolve() if allowed_root else None

    # === 내부 유틸 ============================================

    def _validate_path(self, path: str) -> Path:
        """경로 안전성 검증 후 절대경로 반환."""
        try:
            p = Path(path).expanduser().resolve()
        except (OSError, ValueError) as exc:
            raise FileOperationError(f"잘못된 경로 형식: {path}") from exc

        path_lower = str(p).lower().replace("\\", "\\")
        blocked = self.BLOCKED_DIRS_WIN if self.is_windows else self.BLOCKED_DIRS_UNIX
        for blocked_dir in blocked:
            if path_lower.startswith(blocked_dir.lower()):
                raise FileOperationError(f"시스템 폴더 접근 차단: {p}")

        if self.allowed_root is not None:
            try:
                p.relative_to(self.allowed_root)
            except ValueError:
                raise FileOperationError(
                    f"허용 영역 외부 접근 차단: {p} (허용 루트: {self.allowed_root})"
                )

        return p

    @staticmethod
    def _fmt_time(ts: float) -> str:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _fmt_size(num_bytes: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if num_bytes < 1024:
                return f"{num_bytes:.1f}{unit}"
            num_bytes /= 1024
        return f"{num_bytes:.1f}TB"

    # === 공개 메서드 ==========================================

    def list_dir(self, path: str, pattern: str = "*") -> dict:
        """디렉토리 내용을 조회한다."""
        try:
            p = self._validate_path(path)
        except FileOperationError as e:
            return {"status": "blocked", "message": str(e)}

        if not p.exists():
            return {"status": "error", "message": f"경로 없음: {p}"}
        if not p.is_dir():
            return {"status": "error", "message": f"디렉토리가 아님: {p}"}

        items = []
        for child in sorted(p.glob(pattern)):
            try:
                st = child.stat()
                items.append({
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": st.st_size if child.is_file() else None,
                    "size_h": self._fmt_size(st.st_size) if child.is_file() else "-",
                    "modified": self._fmt_time(st.st_mtime),
                })
            except (OSError, PermissionError):
                continue
            if len(items) >= self.MAX_LIST_ITEMS:
                break

        return {"status": "ok", "path": str(p), "count": len(items), "items": items}

    def read_file(self, path: str, max_chars: Optional[int] = None) -> dict:
        """텍스트 파일을 읽는다. 인코딩 자동 감지."""
        try:
            p = self._validate_path(path)
        except FileOperationError as e:
            return {"status": "blocked", "message": str(e)}

        if not p.exists():
            return {"status": "error", "message": f"파일 없음: {p}"}
        if not p.is_file():
            return {"status": "error", "message": f"파일이 아님: {p}"}

        size = p.stat().st_size
        if size > self.MAX_READ_BYTES:
            return {
                "status": "error",
                "message": f"파일이 너무 큼 ({self._fmt_size(size)} > 10MB)",
            }

        for enc in self.READ_ENCODINGS:
            try:
                content = p.read_text(encoding=enc)
                if max_chars and len(content) > max_chars:
                    content = content[:max_chars] + f"\n... (총 {len(content):,}자 중 {max_chars:,}자만 표시)"
                return {
                    "status": "ok",
                    "path": str(p),
                    "size": size,
                    "encoding": enc,
                    "content": content,
                }
            except UnicodeDecodeError:
                continue

        return {"status": "error", "message": "지원 인코딩으로 디코딩 실패 (바이너리 파일?)"}

    def info(self, path: str) -> dict:
        """파일/폴더의 메타데이터를 반환한다."""
        try:
            p = self._validate_path(path)
        except FileOperationError as e:
            return {"status": "blocked", "message": str(e)}

        if not p.exists():
            return {"status": "error", "message": f"경로 없음: {p}", "exists": False}

        st = p.stat()
        return {
            "status": "ok",
            "path": str(p),
            "name": p.name,
            "type": "dir" if p.is_dir() else "file",
            "size": st.st_size,
            "size_h": self._fmt_size(st.st_size),
            "created": self._fmt_time(st.st_ctime),
            "modified": self._fmt_time(st.st_mtime),
        }

    def find(self, root: str, pattern: str, recursive: bool = True, max_results: int = 100) -> dict:
        """패턴으로 파일을 검색한다."""
        try:
            p = self._validate_path(root)
        except FileOperationError as e:
            return {"status": "blocked", "message": str(e)}

        if not p.exists() or not p.is_dir():
            return {"status": "error", "message": f"디렉토리가 아님: {p}"}

        glob_fn = p.rglob if recursive else p.glob
        results = []
        try:
            for found in glob_fn(pattern):
                results.append(str(found))
                if len(results) >= max_results:
                    break
        except (OSError, PermissionError) as e:
            return {"status": "error", "message": f"검색 중 에러: {e}"}

        return {
            "status": "ok",
            "root": str(p),
            "pattern": pattern,
            "recursive": recursive,
            "count": len(results),
            "results": results,
        }

    def mkdir(self, path: str) -> dict:
        """디렉토리를 생성한다 (이미 있으면 무시)."""
        try:
            p = self._validate_path(path)
        except FileOperationError as e:
            return {"status": "blocked", "message": str(e)}
        try:
            p.mkdir(parents=True, exist_ok=True)
            return {"status": "ok", "path": str(p), "message": "디렉토리 준비 완료"}
        except OSError as e:
            return {"status": "error", "message": str(e)}

    def copy(self, src: str, dst: str) -> dict:
        """파일이나 폴더를 복사한다."""
        try:
            src_p = self._validate_path(src)
            dst_p = self._validate_path(dst)
        except FileOperationError as e:
            return {"status": "blocked", "message": str(e)}

        if not src_p.exists():
            return {"status": "error", "message": f"원본 없음: {src_p}"}

        try:
            if src_p.is_file():
                dst_p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_p, dst_p)
            else:
                shutil.copytree(src_p, dst_p, dirs_exist_ok=True)
            return {"status": "ok", "src": str(src_p), "dst": str(dst_p)}
        except OSError as e:
            return {"status": "error", "message": str(e)}

    def move(self, src: str, dst: str) -> dict:
        """파일이나 폴더를 이동한다."""
        try:
            src_p = self._validate_path(src)
            dst_p = self._validate_path(dst)
        except FileOperationError as e:
            return {"status": "blocked", "message": str(e)}

        if not src_p.exists():
            return {"status": "error", "message": f"원본 없음: {src_p}"}

        try:
            dst_p.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_p), str(dst_p))
            return {"status": "ok", "from": str(src_p), "to": str(dst_p)}
        except OSError as e:
            return {"status": "error", "message": str(e)}

    def delete(self, path: str, to_trash: bool = True) -> dict:
        """파일이나 폴더를 삭제한다 (기본: 휴지통)."""
        try:
            p = self._validate_path(path)
        except FileOperationError as e:
            return {"status": "blocked", "message": str(e)}

        if not p.exists():
            return {"status": "error", "message": f"경로 없음: {p}"}

        try:
            if to_trash and _TRASH_AVAILABLE:
                send2trash(str(p))
                return {"status": "ok", "path": str(p), "method": "trash"}
            else:
                if p.is_file():
                    p.unlink()
                else:
                    shutil.rmtree(p)
                method = "permanent" if not to_trash else "permanent (send2trash 미설치)"
                return {"status": "ok", "path": str(p), "method": method}
        except OSError as e:
            return {"status": "error", "message": str(e)}


# ===========================================================================
# 데모 실행: python -m src.action.file_ops
# ===========================================================================

def _demo():
    """파일 조작 도구의 9가지 기능을 자동 시연한다."""
    import sys
    import tempfile

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

    def show(result: dict, hide_keys: tuple = ()) -> None:
        """결과를 보기 좋게 출력."""
        for k, v in result.items():
            if k in hide_keys:
                continue
            if isinstance(v, list) and v and isinstance(v[0], dict):
                print(f"  {k}:")
                for item in v[:8]:
                    print(f"    - {item}")
                if len(v) > 8:
                    print(f"    ... ({len(v) - 8}개 더)")
            elif isinstance(v, list):
                print(f"  {k}:")
                for item in v[:8]:
                    print(f"    - {item}")
                if len(v) > 8:
                    print(f"    ... ({len(v) - 8}개 더)")
            else:
                val = str(v)
                if len(val) > 200:
                    val = val[:200] + "... (생략)"
                print(f"  {k}: {val}")

    print()
    print(LINE)
    print("  MOD-TOL-002 파일 조작 도구 데모")
    print(f"  send2trash 사용 가능: {_TRASH_AVAILABLE}")
    print(LINE)

    fo = FileOperations()

    # 1. 임시 작업 폴더 생성
    work_dir = Path(tempfile.gettempdir()) / "eddie_demo_work"
    header(1, f"임시 작업 폴더 생성 — {work_dir}")
    show(fo.mkdir(str(work_dir)))

    # 2. 테스트 파일 작성 (Python 직접, 데모용)
    test_file = work_dir / "hello.txt"
    test_file.write_text("안녕하세요, 정혁님.\nEDDIE 입니다.\n파일 조작 도구가 정상 동작 중입니다.\n", encoding="utf-8")
    sub_dir = work_dir / "보고서"
    sub_dir.mkdir(exist_ok=True)
    (sub_dir / "1분기.md").write_text("# 1분기 보고서\n샘플 내용입니다.\n", encoding="utf-8")
    (sub_dir / "2분기.md").write_text("# 2분기 보고서\n샘플 내용입니다.\n", encoding="utf-8")
    print("  (테스트 파일 3개 작성: hello.txt, 보고서/1분기.md, 보고서/2분기.md)")

    # 3. 디렉토리 목록
    header(2, "list_dir — 작업 폴더 목록")
    show(fo.list_dir(str(work_dir)))

    # 4. 파일 정보
    header(3, "info — hello.txt 메타데이터")
    show(fo.info(str(test_file)))

    # 5. 파일 읽기
    header(4, "read_file — hello.txt 내용 읽기")
    show(fo.read_file(str(test_file)))

    # 6. 파일 검색
    header(5, "find — *.md 파일 재귀 검색")
    show(fo.find(str(work_dir), "*.md", recursive=True))

    # 7. 파일 복사
    header(6, "copy — hello.txt → hello_backup.txt")
    backup = work_dir / "hello_backup.txt"
    show(fo.copy(str(test_file), str(backup)))

    # 8. 파일 이동
    header(7, "move — hello_backup.txt → 보고서/")
    moved = sub_dir / "hello_backup.txt"
    show(fo.move(str(backup), str(moved)))

    # 9. 안전 가드 시연 — 시스템 폴더 접근
    header(8, "안전 가드 시연 — C:\\Windows 접근 시도")
    show(fo.list_dir(r"C:\Windows"))

    # 10. 안전 가드 시연 — 잘못된 경로
    header(9, "안전 가드 시연 — 없는 경로")
    show(fo.info(str(work_dir / "없는파일.txt")))

    # 정리 — 작업 폴더 휴지통으로
    header(10, f"정리 — {work_dir} 휴지통으로 이동")
    result = fo.delete(str(work_dir), to_trash=True)
    show(result)

    print()
    print(LINE)
    print("  데모 완료")
    print(LINE)


if __name__ == "__main__":
    _demo()
