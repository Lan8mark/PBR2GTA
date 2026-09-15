from __future__ import annotations

import argparse
import json
import tempfile
import traceback
from pathlib import Path

import cv2
import numpy as np

from .dds import NORMAL_PROFILE, NvttCompressor
from .runner import RequestError, run_request


def _emit(value: object) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pbr2gta-core")
    parser.add_argument("request", type=Path, nargs="?")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--probe-nvtt", type=Path)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.probe_nvtt:
            executable = args.probe_nvtt.expanduser().resolve()
            if not executable.is_file():
                raise RequestError("NVTT nvcompress.exe was not found.")
            with tempfile.TemporaryDirectory(prefix="pbr2gta-nvtt-probe-") as temporary:
                root = Path(temporary)
                source = root / "normal.png"
                image = np.full((4, 4, 3), (255, 128, 128), dtype=np.uint8)
                if not cv2.imwrite(str(source), image):
                    raise RuntimeError("Could not create the NVTT probe image.")
                target = root / "normal.dds"
                header = NvttCompressor(executable, timeout_seconds=60).compress(
                    source, target, NORMAL_PROFILE
                )
                _emit(
                    {
                        "type": "probe",
                        "ok": True,
                        "fourcc": header.fourcc.decode("ascii"),
                        "mip_count": header.mip_count,
                    }
                )
                return 0
        if args.request is None:
            parser.error("request is required unless --probe-nvtt is used")
        request = json.loads(args.request.read_text(encoding="utf-8"))
        result = run_request(request, progress=_emit)
        if args.result:
            args.result.parent.mkdir(parents=True, exist_ok=True)
            args.result.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        _emit({"type": "result", "result": result})
        return 0
    except (RequestError, ValueError, OSError) as exc:
        _emit({"type": "error", "message": str(exc)})
        return 2
    except Exception:  # noqa: BLE001 - process boundary must always return structured JSON.
        message = traceback.format_exc() if args.debug else "Local conversion failed unexpectedly."
        _emit({"type": "error", "message": message})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
