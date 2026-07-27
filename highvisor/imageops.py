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
