"""Extract Cargo Hunters item/icon images from Unity AssetBundles.

This script uses UnityPy to read Unity bundle files and export Sprite/Texture2D
assets as PNG files. It is intentionally standalone so the save editor can stay
stdlib-only; install the optional extraction dependency with --install-deps.

Typical use from this folder:

    python extract_item_icons.py --install-deps
    python extract_item_icons.py

Output defaults to ./exported_icons and includes an icon_manifest.csv file that
records the source bundle and original Unity asset name for every exported PNG.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_GAME_DIR = Path(r"D:\Games\Cargo.Hunters.v0.26.26.43")
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "exported_icons"
DEFAULT_PATTERNS = [
    "spritesgroup_assets_all*.bundle",
    "uigroupatlases_assets_atlases/*.bundle",
    "itemsgroup_assets_cases*.bundle",
    "itemsgroup_assets_keys*.bundle",
    "itemsgroup_assets_loot*.bundle",
    "itemsgroup_assets_outfits*.bundle",
    "itemsgroup_assets_tools*.bundle",
    "itemsgroup_assets_weapons*.bundle",
]
TECHNICAL_TEXTURE_MARKERS = (
    "specular",
    "normal",
    "metallic",
    "occlusion",
    "emission",
    "roughness",
    "smoothness",
)


@dataclass(frozen=True)
class ExportedAsset:
    asset_type: str
    asset_name: str
    source_bundle: str
    output_path: Path
    path_id: str
    width: int | str = ""
    height: int | str = ""


def _bundles_dir_from_game_dir(game_dir: Path) -> Path:
    return game_dir / "CargoHunters_Data" / "StreamingAssets" / "aa" / "StandaloneWindows64"


def _ensure_unitypy(install_deps: bool) -> None:
    if importlib.util.find_spec("UnityPy") is not None:
        return
    if not install_deps:
        raise SystemExit(
            "UnityPy is not installed. Re-run with --install-deps, or install it manually with:\n"
            f"  {sys.executable} -m pip install UnityPy Pillow"
        )
    print("Installing optional extraction dependencies: UnityPy Pillow")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "UnityPy", "Pillow"])


def _safe_filename(value: str, *, fallback: str) -> str:
    cleaned = (value or fallback).strip()
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:160] or fallback


def _unique_path(path: Path, used: set[Path], overwrite: bool) -> Path:
    if overwrite:
        used.add(path)
        return path
    if path not in used and not path.exists():
        used.add(path)
        return path
    stem = path.stem
    suffix = path.suffix
    for n in range(2, 100_000):
        candidate = path.with_name(f"{stem}_{n}{suffix}")
        if candidate not in used and not candidate.exists():
            used.add(candidate)
            return candidate
    raise RuntimeError(f"Could not create a unique filename for {path}")


def _find_bundle_files(bundles_dir: Path, patterns: Iterable[str], all_bundles: bool) -> list[Path]:
    if all_bundles:
        files = sorted(p for p in bundles_dir.rglob("*.bundle") if p.is_file())
    else:
        files = []
        seen: set[Path] = set()
        for pattern in patterns:
            for path in bundles_dir.glob(pattern):
                if path.is_file() and path not in seen:
                    files.append(path)
                    seen.add(path)
        files.sort()
    return files


def _object_name(obj_data: object, path_id: str) -> str:
    for attr in ("name", "m_Name"):
        value = getattr(obj_data, attr, None)
        if value:
            return str(value)
    return f"asset_{path_id}"


def _image_size(image: object) -> tuple[int | str, int | str]:
    size = getattr(image, "size", None)
    if isinstance(size, tuple) and len(size) == 2:
        return size[0], size[1]
    return "", ""


def _is_technical_texture(asset_name: str) -> bool:
    lowered = asset_name.lower()
    return any(marker in lowered for marker in TECHNICAL_TEXTURE_MARKERS)


def export_images(
    *,
    bundles_dir: Path,
    output_dir: Path,
    patterns: list[str],
    all_bundles: bool,
    asset_types: set[str],
    min_size: int,
    limit: int | None,
    overwrite: bool,
    include_technical_maps: bool,
    dry_run: bool,
) -> list[ExportedAsset]:
    if not bundles_dir.exists():
        raise SystemExit(f"Bundle folder not found: {bundles_dir}")

    bundle_files = _find_bundle_files(bundles_dir, patterns, all_bundles)
    if not bundle_files:
        raise SystemExit(f"No .bundle files matched in {bundles_dir}")

    print(f"Bundle folder: {bundles_dir}")
    print(f"Matched bundles: {len(bundle_files)}")
    for path in bundle_files[:20]:
        print(f"  - {path.relative_to(bundles_dir)}")
    if len(bundle_files) > 20:
        print(f"  ... and {len(bundle_files) - 20} more")
    if dry_run:
        return []

    import UnityPy  # type: ignore[import-not-found]

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "icon_manifest.csv"
    used_paths: set[Path] = set()
    exported: list[ExportedAsset] = []
    scanned_objects = 0

    def write_manifest() -> None:
        with manifest_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["asset_type", "asset_name", "source_bundle", "output_path", "path_id", "width", "height"],
            )
            writer.writeheader()
            for item in exported:
                writer.writerow({
                    "asset_type": item.asset_type,
                    "asset_name": item.asset_name,
                    "source_bundle": item.source_bundle,
                    "output_path": str(item.output_path),
                    "path_id": item.path_id,
                    "width": item.width,
                    "height": item.height,
                })

    for bundle_index, bundle_path in enumerate(bundle_files, start=1):
        rel_bundle = str(bundle_path.relative_to(bundles_dir))
        print(f"[{bundle_index}/{len(bundle_files)}] {rel_bundle}")
        try:
            env = UnityPy.load(str(bundle_path))
        except Exception as exc:  # noqa: BLE001
            print(f"  ! Could not load bundle: {type(exc).__name__}: {exc}")
            continue

        for obj in env.objects:
            type_name = getattr(getattr(obj, "type", None), "name", "")
            if type_name not in asset_types:
                continue
            scanned_objects += 1
            try:
                data = obj.read()
                image = getattr(data, "image", None)
                if image is None:
                    continue
                width, height = _image_size(image)
                if isinstance(width, int) and isinstance(height, int):
                    if width < min_size or height < min_size:
                        continue
                path_id = str(getattr(obj, "path_id", ""))
                asset_name = _object_name(data, path_id)
                if not include_technical_maps and _is_technical_texture(asset_name):
                    continue
                safe_name = _safe_filename(asset_name, fallback=f"{type_name}_{path_id}")
                type_dir = output_dir / type_name
                type_dir.mkdir(parents=True, exist_ok=True)
                out_path = _unique_path(type_dir / f"{safe_name}.png", used_paths, overwrite)
                image.save(out_path)
                exported.append(ExportedAsset(type_name, asset_name, rel_bundle, out_path, path_id, width, height))
                if limit is not None and len(exported) >= limit:
                    print(f"Reached --limit {limit}; stopping early.")
                    write_manifest()
                    print(f"Exported PNGs: {len(exported)}")
                    print(f"Manifest: {manifest_path}")
                    return exported
            except Exception as exc:  # noqa: BLE001
                print(f"  ! Could not export {type_name}: {type(exc).__name__}: {exc}")
                continue

    print(f"Scanned matching objects: {scanned_objects}")
    write_manifest()

    print(f"Exported PNGs: {len(exported)}")
    print(f"Manifest: {manifest_path}")
    return exported


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Cargo Hunters Unity Sprite/Texture2D icons to PNG files.")
    parser.add_argument("--game-dir", type=Path, default=DEFAULT_GAME_DIR, help="Cargo Hunters install folder.")
    parser.add_argument("--bundles-dir", type=Path, default=None, help="Override the Unity Addressables bundle folder.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output folder for exported PNGs.")
    parser.add_argument("--pattern", action="append", default=None, help="Bundle glob to include. Can be repeated.")
    parser.add_argument("--all-bundles", action="store_true", help="Scan every .bundle file under the bundle folder.")
    parser.add_argument("--types", nargs="+", default=["Sprite", "Texture2D"], help="Unity asset types to export.")
    parser.add_argument("--min-size", type=int, default=8, help="Skip images smaller than this width/height.")
    parser.add_argument("--limit", type=int, default=None, help="Stop after exporting this many images; useful for testing.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing PNGs instead of generating unique names.")
    parser.add_argument("--include-technical-maps", action="store_true", help="Also export specular/normal/metallic-style texture maps.")
    parser.add_argument("--dry-run", action="store_true", help="Only list matched bundles; do not export anything.")
    parser.add_argument("--install-deps", action="store_true", help="Install UnityPy and Pillow with pip if missing.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.dry_run:
        _ensure_unitypy(args.install_deps)

    bundles_dir = args.bundles_dir or _bundles_dir_from_game_dir(args.game_dir)
    patterns = args.pattern or DEFAULT_PATTERNS
    exported = export_images(
        bundles_dir=bundles_dir,
        output_dir=args.out,
        patterns=patterns,
        all_bundles=args.all_bundles,
        asset_types=set(args.types),
        min_size=args.min_size,
        limit=args.limit,
        overwrite=args.overwrite,
        include_technical_maps=args.include_technical_maps,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print("Dry run complete. Remove --dry-run to export PNGs.")
    elif not exported:
        print("No images exported. Try --all-bundles or lower --min-size.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
