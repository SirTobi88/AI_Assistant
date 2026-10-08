#!/usr/bin/env python3
"""Regenerate the Styrene UI fonts with German characters (Latin-1 + a few
typographic marks) and patch lv_font_conv's output for LVGL 9.

    python3 tools/gen_fonts.py --conv <path/to/lv_font_conv> --assets <dir with StyreneB-Regular.otf>

The font file is Anthropic's and is not in this repo; AI_Limitmeter/assets has it.
Line height and baseline are kept from the existing files, so the screens laid
out for the ASCII fonts don't move; accents may reach a little above the line.
"""
import argparse
import pathlib
import re
import subprocess

RANGES = "0x20-0x7E,0xA0-0xFF,0x2013-0x2014,0x201C,0x201E,0x2026"
SIZES = (12, 14, 16, 20, 24, 28)
SRC = pathlib.Path(__file__).resolve().parent.parent / "firmware" / "src"


def metric(text: str, name: str) -> str | None:
    m = re.search(rf"\.{name} = (-?\d+)", text)
    return m.group(1) if m else None


def patch(text: str, name: str, keep: dict) -> str:
    text = re.sub(r"#if LVGL_VERSION_MAJOR == 8\n/\*Store all the custom data of the font\*/\n"
                  r"static\s+lv_font_fmt_txt_glyph_cache_t cache;\n#endif\n", "", text)
    text = text.replace("#if LVGL_VERSION_MAJOR >= 8\nstatic const lv_font_fmt_txt_dsc_t font_dsc = {\n"
                        "#else\nstatic lv_font_fmt_txt_dsc_t font_dsc = {\n#endif\n",
                        "static const lv_font_fmt_txt_dsc_t font_dsc = {\n")
    text = text.replace(",\n#if LVGL_VERSION_MAJOR == 8\n    .cache = &cache\n#endif\n", "\n")
    text = text.replace(f"#if LVGL_VERSION_MAJOR >= 8\nconst lv_font_t {name} = {{\n#else\n"
                        f"lv_font_t {name} = {{\n#endif\n", f"const lv_font_t {name} = {{\n")
    text = re.sub(r"#if !\(LVGL_VERSION_MAJOR == 6 && LVGL_VERSION_MINOR == 0\)\n(.*?)\n#endif\n", r"\1\n", text)
    text = re.sub(r"#if LV_VERSION_CHECK\(7, 4, 0\) \|\| LVGL_VERSION_MAJOR >= 8\n(.*?\n.*?)\n#endif\n", r"\1\n", text)
    text = re.sub(r"#if LV_VERSION_CHECK\(8, 2, 0\) \|\| LVGL_VERSION_MAJOR >= 9\n(.*?)\n#endif\n", r"\1\n", text)
    text = re.sub(r"(    \.get_glyph_bitmap = lv_font_get_bitmap_fmt_txt,[^\n]*\n)",
                  r"\1    .release_glyph = NULL,\n", text)
    text = text.replace("    .subpx = LV_FONT_SUBPX_NONE,",
                        "    .subpx = LV_FONT_SUBPX_NONE,\n    .kerning = 0,\n    .static_bitmap = 0,")
    for key, val in keep.items():
        if val is not None:
            text = re.sub(rf"\.{key} = -?\d+", f".{key} = {val}", text, count=1)
    if "LVGL_VERSION_MAJOR" in text.split("ALL CUSTOM DATA", 1)[-1]:
        raise SystemExit(f"{name}: unpatched version guard left, check lv_font_conv output")
    return text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conv", required=True)
    ap.add_argument("--assets", required=True)
    args = ap.parse_args()
    otf = pathlib.Path(args.assets) / "StyreneB-Regular.otf"
    for size in SIZES:
        name = f"font_styrene_{size}"
        out = SRC / f"{name}.c"
        old = out.read_text() if out.exists() else ""
        keep = {"line_height": metric(old, "line_height"), "base_line": metric(old, "base_line")}
        subprocess.run([args.conv, "--font", str(otf), "-r", RANGES, "--size", str(size),
                        "--format", "lvgl", "--bpp", "4", "--no-compress", "-o", str(out),
                        "--lv-include", "lvgl.h"], check=True)
        out.write_text(patch(out.read_text(), name, keep))
        print(f"{name}: ok (line_height {keep['line_height']}, base_line {keep['base_line']})")


if __name__ == "__main__":
    main()
