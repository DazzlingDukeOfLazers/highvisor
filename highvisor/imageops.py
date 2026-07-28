"""Client-side image analysis: zone extraction + visual diff/scoring.

These operate on PNGs you've already captured with ``hv shot``; they are pure
Pillow and never touch the daemon or the wire protocol. That's deliberate —
segmentation and comparison are local analysis, not backend actions, so keeping
them here means the daemon stays a thin actuator and the diff loop can run
anywhere the captures live.

Two primitives, matching the two halves of the Ersatz loop:
  - ``detect_zones`` — "segment the screenshot into zones": find the saturated
    colour rectangles (top bar, panels) and return their bounding boxes. No
    hard-coded colours; it discovers the dominant fills itself.
  - ``diff`` — "score the reconstruction against the source": a per-pixel match
    percentage plus an optional amplified heatmap of where they diverge. Scores
    the whole window and, separately, the content below the OS title bar (whose
    text legitimately differs between two apps).
"""
from typing import List, Optional


def _load(path):
    from PIL import Image
    return Image.open(path).convert("RGB")


def diff(a_path: str, b_path: str, crop_top: int = 58,
         out: Optional[str] = None) -> dict:
    """Compare two same-framed captures. Returns match %/mean-abs-error for the
    full window and for the content region (below ``crop_top`` px of chrome). If
    ``out`` is given, writes a x4-amplified difference heatmap there."""
    from PIL import ImageChops, ImageStat
    a, b = _load(a_path), _load(b_path)
    if a.size != b.size:
        a = a.resize(b.size)
    w, h = b.size

    def score(x, y):
        d = ImageChops.difference(x, y)
        mean = sum(ImageStat.Stat(d).mean) / 3.0        # 0..255 avg abs error
        return d, mean, 100.0 * (1.0 - mean / 255.0)

    _, full_err, full_m = score(a, b)
    box = (0, min(crop_top, h), w, h)
    dc, cont_err, cont_m = score(a.crop(box), b.crop(box))
    if out:
        dc.point(lambda p: min(255, p * 4)).save(out)
    return {"size": [w, h],
            "full_match": round(full_m, 2), "full_err": round(full_err, 2),
            "content_match": round(cont_m, 2), "content_err": round(cont_err, 2),
            "crop_top": crop_top, "heatmap": out}


def regions(a_path: str, b_path: str, crop_top: int = 58, grid=(6, 6),
            threshold: float = 92.0, top_n: int = 12, out: Optional[str] = None) -> dict:
    """Localize WHERE two captures diverge: tile the content region into a grid, score each
    cell's match %, and return the worst cells (below ``threshold``) as a ranked punch-list of
    {bbox, match}. If ``out`` is given, write the reference blended with an amplified diff
    heatmap and red boxes + match labels on the worst cells. This is mechanical pixel
    divergence — it says WHERE to look, not WHAT is wrong (font vs colour vs layout)."""
    from PIL import Image, ImageChops, ImageStat, ImageDraw
    a, b = _load(a_path), _load(b_path)
    if a.size != b.size:
        a = a.resize(b.size)
    w, h = b.size
    y0 = min(max(0, crop_top), h)
    diff = ImageChops.difference(a, b)
    gx, gy = grid
    cw = max(1, w // gx)
    ch = max(1, (h - y0) // gy)
    cells = []
    for j in range(gy):
        for i in range(gx):
            x = i * cw
            y = y0 + j * ch
            x2 = w if i == gx - 1 else x + cw
            y2 = h if j == gy - 1 else y + ch
            mean = sum(ImageStat.Stat(diff.crop((x, y, x2, y2))).mean) / 3.0
            cells.append({"bbox": [x, y, x2 - x, y2 - y], "match": round(100.0 * (1.0 - mean / 255.0), 2)})
    cells.sort(key=lambda c: c["match"])
    worst = [c for c in cells if c["match"] < threshold][:top_n]
    overall = round(sum(c["match"] for c in cells) / max(1, len(cells)), 2)
    if out:
        heat = diff.point(lambda p: min(255, p * 4)).convert("RGB")
        canvas = Image.blend(b, heat, 0.45)
        d = ImageDraw.Draw(canvas)
        for c in worst:
            x, y, ww, hh = c["bbox"]
            d.rectangle([x, y, x + ww - 1, y + hh - 1], outline=(255, 70, 70), width=3)
            d.text((x + 4, y + 3), "%.0f%%" % c["match"], fill=(255, 220, 120))
        canvas.save(out)
    return {"size": [w, h], "grid": [gx, gy], "overall_match": overall,
            "worst": worst, "annotated": out}


def sidebyside(a_path: str, b_path: str, out: str, label_a: str = "A",
               label_b: str = "B", height: int = 760) -> str:
    """Scale two captures to a common height and lay them side by side, labelled."""
    from PIL import Image, ImageDraw
    a, b = _load(a_path), _load(b_path)
    def sc(im):
        return im.resize((max(1, round(im.width * height / im.height)), height))
    a, b = sc(a), sc(b)
    gap, top = 14, 24
    c = Image.new("RGB", (a.width + b.width + gap, height + top), (18, 18, 18))
    c.paste(a, (0, top))
    c.paste(b, (a.width + gap, top))
    d = ImageDraw.Draw(c)
    d.text((6, 7), label_a, fill=(210, 210, 170))
    d.text((a.width + gap + 6, 7), label_b, fill=(210, 210, 170))
    c.save(out)
    return out


def stack_vertical(paths: List[str], out: str, pad: int = 10,
                   bg=(20, 20, 20)) -> Optional[str]:
    """Stack images into one tall contact sheet (each normalized to a common width)."""
    from PIL import Image
    ims = [_load(p) for p in paths]
    if not ims:
        return None
    w = max(im.width for im in ims)
    ims = [im if im.width == w else im.resize((w, round(im.height * w / im.width))) for im in ims]
    total = sum(im.height for im in ims) + pad * (len(ims) + 1)
    c = Image.new("RGB", (w + 2 * pad, total), bg)
    y = pad
    for im in ims:
        c.paste(im, (pad, y))
        y += im.height + pad
    c.save(out)
    return out


def detect_zones(path: str, top: int = 0, tol: int = 40,
                 min_frac: float = 0.003, sat_min: int = 35) -> List[dict]:
    """Find the saturated colour rectangles in an image. Skips greys/near-black
    (background, chrome). Returns [{hex, rgb, bbox:[l,t,r,b], coverage}], the
    largest first. ``top`` crops OS chrome before analysis; bboxes are reported
    back in full-image coordinates."""
    from PIL import ImageChops
    img = _load(path)
    if top:
        img = img.crop((0, top, img.width, img.height))
    w, h = img.size
    total = w * h

    # Dominant colours from a downscaled copy (fast), most-common first.
    small = img.resize((max(1, w // 8), max(1, h // 8)))
    colors = small.getcolors(maxcolors=1 << 20) or []
    colors.sort(reverse=True)
    picked = []
    for _count, rgb in colors:
        if max(rgb) - min(rgb) < sat_min:                    # skip grey/bg
            continue
        if any(sum(abs(c - p) for c, p in zip(rgb, q)) < 60 for q in picked):
            continue                                         # merge near-dupes
        picked.append(rgb)
        if len(picked) >= 12:
            break

    R, G, B = img.split()

    def band(ch, c):
        lo, hi = c - tol, c + tol
        return ch.point(lambda p: 255 if lo <= p <= hi else 0)

    zones = []
    for rgb in picked:
        m = ImageChops.multiply(ImageChops.multiply(band(R, rgb[0]),
                                                    band(G, rgb[1])),
                                band(B, rgb[2]))
        area = m.histogram()[255]
        if area < total * min_frac:
            continue
        bb = m.getbbox()
        if not bb:
            continue
        # Report the fill sampled at the box centre, not the seed colour (which
        # may be an anti-aliased edge shade), so the hex is the true panel fill.
        fill = img.getpixel(((bb[0] + bb[2]) // 2, (bb[1] + bb[3]) // 2))
        zones.append({"hex": "#%02x%02x%02x" % fill, "rgb": list(fill),
                      "bbox": [bb[0], bb[1] + top, bb[2], bb[3] + top],
                      "coverage": round(area / total, 4)})
    zones.sort(key=lambda z: -z["coverage"])

    # Collapse near-identical rectangles (a fill and its anti-aliased edge often
    # survive as two close colours over the same box) — keep the largest.
    kept = []
    for z in zones:
        if any(all(abs(c - k) <= 12 for c, k in zip(z["bbox"], o["bbox"]))
               for o in kept):
            continue
        kept.append(z)
    return kept
