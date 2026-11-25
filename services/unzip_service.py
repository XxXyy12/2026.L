"""压缩包解压与图片枚举：zip 优先，7z/rar 兜底。"""

from __future__ import annotations

import zipfile
from pathlib import Path

from config import Config
from core.errors import AppError, ErrorCode


class UnzipService:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.extensions = tuple(str(e).lower() for e in cfg.download.image_extensions)

    def extract(self, archive: Path, target_dir: Path) -> list[Path]:
        """解压到 target_dir，返回图片文件列表（按路径排序）。"""
        target_dir.mkdir(parents=True, exist_ok=True)
        suffix = archive.suffix.lower()
        try:
            if suffix == ".zip":
                self._extract_zip(archive, target_dir)
            elif suffix == ".7z":
                self._extract_7z(archive, target_dir)
            elif suffix == ".rar":
                self._extract_rar(archive, target_dir)
            else:
                raise AppError(ErrorCode.E_UNZIP, f"不支持的压缩格式：{suffix}")
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AppError(ErrorCode.E_UNZIP, f"解压失败：{type(exc).__name__}: {exc}") from exc
        return self.list_images(target_dir)

    def _extract_zip(self, archive: Path, target_dir: Path) -> None:
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                self._safe_extract(zf, member, target_dir)

    @staticmethod
    def _safe_extract(zf: zipfile.ZipFile, member: zipfile.ZipInfo, target_dir: Path) -> None:
        destination = (target_dir / member.filename).resolve()
        if not str(destination).startswith(str(target_dir.resolve())):
            raise AppError(ErrorCode.E_UNZIP, f"压缩包包含非法路径：{member.filename}")
        if member.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, destination.open("wb") as dst:
            dst.write(src.read())

    def _extract_7z(self, archive: Path, target_dir: Path) -> None:
        import py7zr

        with py7zr.SevenZipFile(archive, mode="r") as zf:
            zf.extractall(path=str(target_dir))

    def _extract_rar(self, archive: Path, target_dir: Path) -> None:
        import rarfile  # 需要系统安装 unrar/bsdtar

        with rarfile.RarFile(archive) as rf:
            rf.extractall(path=str(target_dir))

    def list_images(self, root: Path) -> list[Path]:
        images = [
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in self.extensions
        ]
        images.sort(key=lambda p: (len(p.parts), str(p)))
        return images
