from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .converter import convert_material, convert_spec2gta, pack_linear_spec_gloss
from .profile import DEFAULT_PROFILE


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pbr2gta-lite",
        description="BaseColor + Metallic + Roughness -> GTA diffuse, Specular RG и параметры.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert = subparsers.add_parser("convert", help="Конвертировать один материал")
    convert.add_argument("--base-color", required=True)
    convert.add_argument("--metallic", required=True)
    convert.add_argument("--roughness", required=True)
    convert.add_argument("--output", required=True)
    convert.add_argument("--stem")
    convert.add_argument("--specular-mode", choices=("weapon", "default"), default="weapon")
    convert.add_argument("--overwrite", action="store_true")

    spec2gta = subparsers.add_parser(
        "spec2gta",
        help="Diffuse + Specular + Gloss PNG -> GTA weapon/default specular layouts",
    )
    spec2gta.add_argument("--diffuse", required=True)
    spec2gta.add_argument("--specular", required=True)
    spec2gta.add_argument("--gloss", required=True)
    spec2gta.add_argument("--output", required=True)
    spec2gta.add_argument("--stem")
    spec2gta.add_argument("--output-mode", choices=("weapon", "default", "both"), default="weapon")
    spec2gta.add_argument("--overwrite", action="store_true")

    pack = subparsers.add_parser(
        "pack-linear-spec-gloss",
        help="Pack linear Spec/Gloss grayscale PNG into GTA Specular RG PNG",
    )
    pack.add_argument("--spec", required=True)
    pack.add_argument("--gloss", required=True)
    pack.add_argument("--output", required=True)
    pack.add_argument("--mode", choices=("weapon", "default"), default="weapon")
    pack.add_argument("--overwrite", action="store_true")

    subparsers.add_parser("dump-profile", help="Показать фиксированные параметры")
    subparsers.add_parser("gui", help="Открыть графический интерфейс")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "dump-profile":
        print(json.dumps(DEFAULT_PROFILE.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "gui":
        from .gui import main as gui_main

        gui_main()
        return 0

    if args.command == "pack-linear-spec-gloss":
        try:
            result = pack_linear_spec_gloss(
                spec_path=args.spec,
                gloss_path=args.gloss,
                output_path=args.output,
                mode=args.mode,
                overwrite=args.overwrite,
            )
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

        print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
        return 0

    if args.command == "spec2gta":
        try:
            result = convert_spec2gta(
                diffuse_path=args.diffuse,
                specular_path=args.specular,
                gloss_path=args.gloss,
                output_directory=args.output,
                stem=args.stem,
                output_mode=args.output_mode,
                overwrite=args.overwrite,
                progress_callback=lambda phase, value: print(
                    f"[{value * 100:5.1f}%] {phase}", file=sys.stderr
                ),
            )
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

        print(json.dumps(result.parameters, ensure_ascii=False, indent=2))
        print(f"\nFiles: {Path(result.output_directory)}")
        return 0

    try:
        result = convert_material(
            base_color_path=args.base_color,
            metallic_path=args.metallic,
            roughness_path=args.roughness,
            output_directory=args.output,
            stem=args.stem,
            specular_mode=args.specular_mode,
            overwrite=args.overwrite,
            progress_callback=lambda phase, value: print(
                f"[{value * 100:5.1f}%] {phase}", file=sys.stderr
            ),
        )
    except Exception as exc:  # CLI boundary: render a compact actionable error.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result.parameters, ensure_ascii=False, indent=2))
    print(f"\nФайлы: {Path(result.output_directory)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
