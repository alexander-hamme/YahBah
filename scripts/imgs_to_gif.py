#!/usr/bin/env python3
import argparse
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}


def get_image_files(folder: Path, recursive: bool = False, match_patt: str | None = None) -> List[Path]:
    paths = folder.rglob("*") if recursive else folder.iterdir()
    files = [p for p in paths if p.is_file() and p.suffix.lower() in IMAGE_EXTS]

    if match_patt:
        files = [p for p in files if p.match(match_patt)]

    if not files:
        raise ValueError(f"No matching image files found in: {folder}")
    return files


def sort_by_name(files: List[Path]) -> List[Path]:
    return sorted(files, key=lambda p: p.name)


def sort_by_timestamp(files: List[Path], use: str = "mtime") -> List[Path]:
    attr_map = {
        "mtime": ("st_mtime_ns", "st_mtime"),
        "ctime": ("st_ctime_ns", "st_ctime"),
        "atime": ("st_atime_ns", "st_atime"),
    }
    ns_attr, fallback_attr = attr_map[use]

    def key(p: Path) -> Tuple[int, str]:
        st = p.stat()
        t = getattr(st, ns_attr, None)
        if t is None:
            t = int(getattr(st, fallback_attr) * 1_000_000_000)
        return (t, p.name)

    return sorted(files, key=key)


def build_filter(fps: float, width: int | None, height: int | None, colors: int, dither: str) -> str:
    if width is None and height is None:
        scale_expr = "scale=iw:ih:flags=lanczos"
    else:
        w = width if width is not None else -1
        h = height if height is not None else -1
        scale_expr = f"scale={w}:{h}:flags=lanczos"

    return (
        f"fps={fps},"
        f"{scale_expr},split[a][b];"
        f"[a]palettegen=max_colors={colors}[p];"
        f"[b][p]paletteuse=dither={dither}"
    )


def write_concat_file(files: List[Path], tmp_path: Path) -> None:
    with open(tmp_path, "w", encoding="utf-8") as f:
        for p in files:
            # ffmpeg concat format: one file per line
            f.write(f"file {shlex.quote(str(p.resolve()))}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Turn a folder of images into a GIF using ffmpeg.")
    parser.add_argument("input_folder", type=Path)
    parser.add_argument("output_gif", type=Path)
    parser.add_argument("--sort", choices=["name", "mtime", "ctime", "atime"], default="name")
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--colors", type=int, default=256)
    parser.add_argument("--dither", default="sierra2_4a",
                        choices=["none", "bayer", "heckbert", "floyd_steinberg", "sierra2", "sierra2_4a"])
    parser.add_argument("--loop", type=int, default=0)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--match_patt", type=str, default=None,
                        help='Glob-style pattern, e.g. "page*.jpeg"')
    args = parser.parse_args()

    try:
        files = get_image_files(args.input_folder, recursive=args.recursive, match_patt=args.match_patt)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # sort
    if args.sort == "name":
        files = sort_by_name(files)
    else:
        files = sort_by_timestamp(files, use=args.sort)

    # clamp colors
    colors = max(2, min(args.colors, 256))

    # build filter
    vf = build_filter(args.fps, args.width, args.height, colors, args.dither)

    # create temp concat file
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
            concat_path = Path(tmp.name)
        write_concat_file(files, concat_path)
    except Exception as e:
        print(f"Failed to create concat file: {e}", file=sys.stderr)
        return 1

    # build ffmpeg command
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_path),
        "-vf", vf,
        "-loop", str(args.loop),
        str(args.output_gif),
    ]

    # run ffmpeg
    try:
        print("Running:", " ".join(shlex.quote(c) for c in cmd))
        result = subprocess.run(cmd)
        return result.returncode
    except Exception as e:
        print(f"Failed to run ffmpeg: {e}", file=sys.stderr)
        return 1
    finally:
        try:
            concat_path.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())