#!/usr/bin/env python3
import argparse


def main(percent, red, green, outfile, minify=False):
    if percent < 10:
        # 0% -- 9%
        width_p = 29
        width_t = 90
        txt_len = 190
        txt_x = 745
    elif percent < 100:
        # 10% -- 99%
        width_p = 35
        width_t = 96
        txt_len = 250
        txt_x = 775
    else:
        # 100%
        width_p = 43
        width_t = 104
        txt_len = 330
        txt_x = 815

    if percent < red:
        color_index = 0
    elif percent > green:
        color_index = 100
    else:
        # color is in range red..green
        color_index = int(round((percent - red) / (green - red) * 100, 0))

    svg_body = TEMPLATE.format(
        word="coverage",
        percent=percent,
        color=COLORS[color_index],
        width_p=width_p,
        width_t=width_t,
        txt_len=txt_len,
        txt_x=txt_x,
    )
    if minify:
        svg_body = "".join(line.lstrip() for line in svg_body.splitlines())
    print(svg_body, file=outfile)


# shields.io has a nice SVG badge
# see: https://www.shields.io/category/coverage
TEMPLATE = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{width_t}" height="20" role="img" aria-label="{word}: {percent}%">
    <title>{word}: {percent}%</title>
    <linearGradient id="s" x2="0" y2="100%">
        <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
        <stop offset="1" stop-opacity=".1"/>
    </linearGradient>
    <clipPath id="r">
        <rect width="{width_t}" height="20" rx="3" fill="#fff"/>
    </clipPath>
    <g clip-path="url(#r)">
        <rect width="61" height="20" fill="#555"/>
        <rect x="61" width="{width_p}" height="20" fill="{color}"/>
        <rect width="{width_t}" height="20" fill="url(#s)"/>
    </g>
    <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" text-rendering="geometricPrecision" font-size="110">
        <text aria-hidden="true" x="315" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="510">{word}</text>
        <text x="315" y="140" transform="scale(.1)" fill="#fff" textLength="510">{word}</text>
        <text aria-hidden="true" x="{txt_x}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="{txt_len}">{percent}%</text>
        <text x="{txt_x}" y="140" transform="scale(.1)" fill="#fff" textLength="{txt_len}">{percent}%</text>
    </g>
</svg>"""  # noqa: E501

# Codecov has a nice color gradient.
# see: https://docs.codecov.com/docs/coverage-configuration
COLORS = [
    "#e05d44", "#e25e43", "#e35f42", "#e56142", "#e76241", "#e86440", "#ea653f",
    "#ec673f", "#ed683e", "#ef6a3d", "#f06c3d", "#f26d3c", "#f36f3b", "#f5713b",
    "#f7733a", "#f87539", "#fa7739", "#fb7938", "#fd7b38", "#fe7d37", "#fe7d37",
    "#fd8035", "#fc8232", "#fb8530", "#fa882e", "#f98b2c", "#f88e29", "#f79127",
    "#f59425", "#f49723", "#f39a21", "#f29d1f", "#f0a01d", "#efa41b", "#eea719",
    "#ecaa17", "#ebad15", "#e7b015", "#e3b116", "#dfb317", "#dfb317", "#dcb317",
    "#d9b318", "#d6b318", "#d3b319", "#cfb319", "#ccb21a", "#c9b21a", "#c6b11a",
    "#c3b11b", "#c0b01b", "#bdb01b", "#baaf1b", "#b7ae1c", "#b4ad1c", "#b2ac1c",
    "#afab1c", "#acaa1d", "#a9a91d", "#a4a61d", "#a4a61d", "#a4a81c", "#a4aa1a",
    "#a4ac19", "#a4ad17", "#a3af16", "#a3b114", "#a3b313", "#a2b511", "#a2b710",
    "#a1b90e", "#a0bb0c", "#9fbc0b", "#9ebe09", "#9dc008", "#9cc206", "#9bc405",
    "#9ac603", "#98c802", "#97ca00", "#97ca00", "#93ca01", "#8eca02", "#8aca02",
    "#85cb03", "#81cb04", "#7dcb05", "#78cb06", "#74cb06", "#70cb07", "#6ccb08",
    "#68cb09", "#63cc0a", "#5fcc0b", "#5bcc0c", "#57cc0c", "#53cc0d", "#4fcc0e",
    "#4ccc0f", "#48cc10", "#44cc11",
]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-m", "--minify", action="store_true", default=False,
                        help="remove indentation and linefeeds from SVG output")
    parser.add_argument("-g", "--green", metavar="N", type=int, default=95,
                        help="coverage above which will always be max "
                             "'green' (default=%(default)s).")
    parser.add_argument("-r", "--red", metavar="N", type=int, default=60,
                        help="coverage below which will always be max 'red' "
                             "(default=%(default)s).")
    parser.add_argument("-o", "--outfile", metavar="FILE",
                        type=argparse.FileType("w"), default="-",
                        help="write output to %(metavar)s (use dash (-) for "
                             "STDOUT, default=STDOUT)")
    parser.add_argument("percent", type=int, help="coverage percent (0-100)")
    opts = parser.parse_args()
    if opts.red > opts.green:
        parser.error(f"red ({opts.red}) must be lower than green "
                     f"({opts.green})")
    for name in ["green", "red", "percent"]:
        value = getattr(opts, name)
        if not (0 <= value <= 100):
            parser.error(f"invalid {name} value: {value} "
                         "(must be between 0 and 100)")
    main(opts.percent, opts.red, opts.green, opts.outfile, opts.minify)
