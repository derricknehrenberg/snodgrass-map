#!/usr/bin/env python3
"""Build a Google Earth KMZ that mirrors the web map.

Reads the same data/ GeoJSON and data/layers_index.json the web map reads, and
reuses the web map's own styling by parsing the LAYER_STYLE / CAT_PALETTE /
MA_COLORS / FIELD_LABELS / TITLE_FIELDS constants straight out of index.html.
Parsing them (rather than restating them here) means the KMZ cannot drift out of
sync with the live map — restyle index.html, rerun this, and the KMZ follows.

Layers held back from publication never reach this script: it only sees what
clip_layers.py actually wrote into data/.

Usage:  python scripts/build_kml.py
Output: downloads/snodgrass_google_earth.kmz
"""
import json
import os
import re
import struct
import sys
import zlib
import zipfile
from xml.sax.saxutils import escape

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(REPO, "index.html")
DATA = os.path.join(REPO, "data")
OUT = os.path.join(REPO, "downloads", "snodgrass_google_earth.kmz")

DOC_NAME = "Snodgrass Recreation Emphasis Area — Stakeholder Map"
DISCLAIMER = ("Draft working map for partner coordination · "
              "not an official survey product. All layers clipped to the draft "
              "Snodgrass coordination boundary. Positions are approximate — verify "
              "with authoritative records before design or legal use.")


# ---------- pull the web map's styling out of index.html ----------

def _js(src, name, opener):
    """Return the literal body of `const <name> = <opener>...<closer>;`."""
    i = src.index(f"const {name} = {opener}") + len(f"const {name} = {opener}")
    closer = {"[": "]", "{": "}"}[opener]
    depth, j = 1, i
    while depth:
        if src[j] == opener:
            depth += 1
        elif src[j] == closer:
            depth -= 1
        j += 1
    return src[i:j - 1]


def parse_index_html(path):
    src = open(path, encoding="utf-8").read()

    palette = re.findall(r'"(#[0-9a-fA-F]{6})"', _js(src, "CAT_PALETTE", "["))

    ma = []
    for pat, col in re.findall(r'\[\s*/(.+?)/\s*,\s*"(#[0-9a-fA-F]{6})"\s*\]',
                               _js(src, "MA_COLORS", "[")):
        ma.append((re.compile(pat), col))

    styles = {}
    for key, body in re.findall(r'"([^"]+)"\s*:\s*\{(.*?)\}', _js(src, "LAYER_STYLE", "{"),
                                re.S):
        cfg = {}
        for k, v in re.findall(r'(\w+)\s*:\s*("(?:[^"]*)"|null|[\w.]+)', body):
            v = v.strip()
            if v == "null":
                cfg[k] = None
            elif v.startswith('"'):
                cfg[k] = v[1:-1]
            elif re.fullmatch(r"\.?\d+(\.\d+)?", v):
                cfg[k] = float(v)
            else:
                cfg[k] = v  # bare identifier, e.g. the maColor function
        styles[key] = cfg

    labels = dict(re.findall(r'(\w+)\s*:\s*"([^"]*)"', _js(src, "FIELD_LABELS", "{")))
    titles = re.findall(r'"([^"]+)"', _js(src, "TITLE_FIELDS", "["))
    return palette, ma, styles, labels, titles


PALETTE, MA_COLORS, LAYER_STYLE, FIELD_LABELS, TITLE_FIELDS = parse_index_html(INDEX)


def ma_color(v):
    v = "" if v is None else str(v)
    for rx, c in MA_COLORS:
        if rx.search(v):
            return c
    return "#9aa0a6"


def cat_colors(features, field):
    """Mirror buildCatColors(): sorted unique values cycle through CAT_PALETTE."""
    vals = sorted({str(f.get("properties", {}).get(field, "—")
                       if f.get("properties", {}).get(field) is not None else "—")
                   for f in features})
    return {v: PALETTE[i % len(PALETTE)] for i, v in enumerate(vals)}


def color_for(cfg, props, catmap):
    """Mirror colorFor() in index.html, in the same precedence order."""
    if cfg.get("fixed"):
        return cfg["fixed"]
    if cfg.get("color") == "maColor":
        return ma_color(props.get(cfg.get("field")))
    if cfg.get("field") and catmap:
        key = props.get(cfg["field"])
        return catmap.get(str(key) if key is not None else "—", "#888888")
    c = cfg.get("color")
    return c if isinstance(c, str) and c.startswith("#") else "#888888"


def kml_color(hex_rgb, opacity=1.0):
    """#rgb or #rrggbb + opacity -> KML aabbggrr."""
    h = str(hex_rgb).lstrip("#")
    if len(h) == 3:  # index.html uses shorthand for some grays, e.g. "#666"
        h = "".join(ch * 2 for ch in h)
    if len(h) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        raise ValueError(f"cannot convert color {hex_rgb!r} to KML")
    r, g, b = h[0:2], h[2:4], h[4:6]
    a = format(max(0, min(255, round(opacity * 255))), "02x")
    return f"{a}{b}{g}{r}".lower()


# ---------- a tiny white dot icon, tinted per-layer by IconStyle ----------

def circle_png(size=48):
    """White anti-aliased disc with a soft edge; stdlib only (no Pillow)."""
    rows = []
    c, r = (size - 1) / 2.0, size / 2.0 - 1.5
    for y in range(size):
        row = bytearray([0])  # filter byte
        for x in range(size):
            d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
            a = 255 if d <= r else (0 if d >= r + 1.5 else int(255 * (1 - (d - r) / 1.5)))
            row += bytes((255, 255, 255, a))
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


# ---------- geometry ----------

def ring(coords):
    return " ".join(f"{c[0]:.6f},{c[1]:.6f},0" for c in coords)


def geom_kml(geom):
    """GeoJSON geometry -> KML. Returns "" for degenerate/empty geometry so the
    caller can drop the feature rather than emit an invalid Placemark."""
    if not geom:
        return ""
    t, c = geom.get("type"), geom.get("coordinates")
    if not c and t != "GeometryCollection":
        return ""
    if t == "Point":
        return f"<Point><altitudeMode>clampToGround</altitudeMode><coordinates>{c[0]:.6f},{c[1]:.6f},0</coordinates></Point>"
    if t == "MultiPoint":
        return "<MultiGeometry>" + "".join(geom_kml({"type": "Point", "coordinates": p}) for p in c) + "</MultiGeometry>"
    if t == "LineString":
        if len(c) < 2:
            return ""
        return ("<LineString><tessellate>1</tessellate><altitudeMode>clampToGround</altitudeMode>"
                f"<coordinates>{ring(c)}</coordinates></LineString>")
    if t == "Polygon":
        if not c or len(c[0]) < 4:  # a LinearRing needs >=4 positions (closed)
            return ""
        parts = [("<Polygon><tessellate>1</tessellate><altitudeMode>clampToGround</altitudeMode>"
                  f"<outerBoundaryIs><LinearRing><coordinates>{ring(c[0])}</coordinates></LinearRing></outerBoundaryIs>")]
        for hole in c[1:]:
            if len(hole) >= 4:
                parts.append(f"<innerBoundaryIs><LinearRing><coordinates>{ring(hole)}</coordinates></LinearRing></innerBoundaryIs>")
        parts.append("</Polygon>")
        return "".join(parts)
    if t in ("MultiLineString", "MultiPolygon"):
        single = "LineString" if t == "MultiLineString" else "Polygon"
        inner = "".join(geom_kml({"type": single, "coordinates": p}) for p in c)
        return f"<MultiGeometry>{inner}</MultiGeometry>" if inner else ""
    if t == "GeometryCollection":
        inner = "".join(geom_kml(g) for g in geom.get("geometries", []))
        return f"<MultiGeometry>{inner}</MultiGeometry>" if inner else ""
    return ""


# ---------- balloon, mirroring popupHtml() ----------

BALLOON_CSS = ("font:13px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a;"
               "max-width:340px")


def describe(layer_title, props):
    title = ""
    for f in TITLE_FIELDS:
        v = props.get(f)
        if v is not None and str(v).strip() != "":
            title = str(v)
            break
    rows = []
    for k, v in props.items():
        if v is None or re.fullmatch(r"[\s,.;:\-]*", str(v)):
            continue
        val = f"{v:.2f}" if isinstance(v, float) and v != int(v) else v
        rows.append(f'<tr><td style="color:#666;padding:2px 10px 2px 0;vertical-align:top;white-space:nowrap">'
                    f'{escape(str(FIELD_LABELS.get(k, k)))}</td>'
                    f'<td style="padding:2px 0">{escape(str(val))}</td></tr>')
    return (f'<div style="{BALLOON_CSS}">'
            f'<div style="text-transform:uppercase;letter-spacing:.06em;font-size:11px;color:#777">{escape(layer_title)}</div>'
            f'<div style="font-weight:600;font-size:15px;margin:2px 0 8px">{escape(title)}</div>'
            f'<table cellpadding="0" cellspacing="0">{"".join(rows)}</table></div>')


# ---------- build ----------

def style_block(sid, cfg, color):
    kind = cfg.get("kind", "poly-outline")
    weight = float(cfg.get("weight") or 1)
    line_col, line_w, poly = color, max(1.0, weight), None

    if kind == "poly-cat":
        line_col, line_w = "#5f5b54", 1.0
        poly = f"<PolyStyle><color>{kml_color(color, float(cfg.get('fillOp') or .4))}</color><fill>1</fill><outline>1</outline></PolyStyle>"
    elif kind == "poly-outline":
        # Web map draws these as outline-only (2% fill); Google Earth reads cleaner unfilled.
        poly = "<PolyStyle><fill>0</fill><outline>1</outline></PolyStyle>"
        line_w = max(1.6, weight)
    elif kind == "point":
        return (f'<Style id="{sid}"><IconStyle><color>{kml_color(color)}</color><scale>0.7</scale>'
                f'<Icon><href>files/dot.png</href></Icon></IconStyle>'
                f'<LabelStyle><scale>0</scale></LabelStyle></Style>')
    else:  # line, line-cat
        line_w = max(1.8, weight)

    # StyleType child order is normative too: Icon, Label, Line, Poly, Balloon, List.
    s = (f'<Style id="{sid}"><LabelStyle><scale>0</scale></LabelStyle>'
         f'<LineStyle><color>{kml_color(line_col)}</color>'
         f'<width>{line_w:g}</width></LineStyle>')
    if poly:
        s += poly
    return s + "</Style>"


# KML 2.2 AbstractFeatureType child sequence. Order is normative: Google Earth
# quietly ignores elements that appear out of sequence (an <open> before
# <visibility> cost us the per-layer on/off state once already), so assert it.
FEATURE_ORDER = ["name", "visibility", "open", "atom:author", "atom:link", "address",
                 "xal:AddressDetails", "phoneNumber", "Snippet", "description",
                 "LookAt", "Camera", "TimeStamp", "TimeSpan", "styleUrl", "Style",
                 "StyleMap", "Region", "Metadata", "ExtendedData"]
KML_NS = "{http://www.opengis.net/kml/2.2}"


def assert_feature_order(kml_text):
    """Raise if any Document/Folder/Placemark lists its header elements out of order."""
    from xml.etree import ElementTree as ET
    root = ET.fromstring(kml_text)
    rank = {n: i for i, n in enumerate(FEATURE_ORDER)}
    checked = 0
    for feat in root.iter():
        tag = feat.tag.replace(KML_NS, "")
        if tag not in ("Document", "Folder", "Placemark"):
            continue
        checked += 1
        seen = -1
        for child in feat:
            ctag = child.tag.replace(KML_NS, "")
            if ctag not in rank:
                continue  # geometry / nested features: they follow the header block
            if rank[ctag] < seen:
                raise AssertionError(
                    f"<{tag} name={feat.findtext(KML_NS + 'name')!r}>: "
                    f"<{ctag}> is out of KML sequence order")
            seen = rank[ctag]
    return checked


def main():
    index = json.load(open(os.path.join(DATA, "layers_index.json")))
    styles, folders, report, fallbacks = [], [], [], []

    # Boundary first, always on.
    bnd = json.load(open(os.path.join(DATA, "boundary", "snodgrass_boundary.geojson")))
    styles.append('<Style id="boundary"><LineStyle><color>ff3d4fd9</color><width>3</width></LineStyle>'
                  '<PolyStyle><fill>0</fill><outline>1</outline></PolyStyle></Style>')
    marks = "".join("<Placemark><name>Coordination boundary</name><visibility>1</visibility>"
                    f"<styleUrl>#boundary</styleUrl>{geom_kml(f['geometry'])}</Placemark>"
                    for f in bnd["features"])
    folders.append("<Folder><name>Snodgrass coordination boundary</name>"
                   f"<visibility>1</visibility><open>0</open>{marks}</Folder>")

    west, south, east, north = 180.0, 90.0, -180.0, -90.0
    for f in bnd["features"]:
        for c in re.findall(r"(-?\d+\.\d+),(-?\d+\.\d+),0", ring(f["geometry"]["coordinates"][0])):
            west, east = min(west, float(c[0])), max(east, float(c[0]))
            south, north = min(south, float(c[1])), max(north, float(c[1]))

    for grp in index["groups"]:
        sub = []
        for lyr in grp["layers"]:
            key = f"{grp['key']}/{lyr['name']}"
            path = os.path.join(REPO, lyr["path"])
            if not os.path.exists(path):
                report.append((key, "MISSING", lyr["path"]))
                continue
            gj = json.load(open(path))
            feats = gj.get("features", [])
            cfg = LAYER_STYLE.get(key)
            if cfg is None:
                cfg = {"kind": "poly-outline", "color": "#888888", "weight": 1}
                fallbacks.append(key)

            catmap = None
            if cfg.get("field") and cfg.get("color") != "maColor" and not cfg.get("fixed"):
                catmap = cat_colors(feats, cfg["field"])

            # One shared Style per distinct colour keeps the KMZ small.
            vis = 1 if lyr.get("default") else 0
            sids, marks, dropped = {}, [], 0
            for f in feats:
                props = f.get("properties") or {}
                gk = geom_kml(f.get("geometry"))
                if not gk:
                    dropped += 1
                    continue
                col = color_for(cfg, props, catmap)
                if col not in sids:
                    sid = f"{grp['key']}_{lyr['name']}_{len(sids)}"
                    sids[col] = sid
                    styles.append(style_block(sid, cfg, col))
                name = ""
                for tf in TITLE_FIELDS:
                    if props.get(tf) not in (None, ""):
                        name = escape(str(props[tf]))
                        break
                # KML feature element order is significant: name, visibility, open,
                # Snippet, description, styleUrl, then the geometry. Google Earth
                # silently ignores out-of-sequence elements — which is what made an
                # earlier build show every layer switched on.
                marks.append(f"<Placemark><name>{name}</name>"
                             f"<visibility>{vis}</visibility>"
                             f"<description><![CDATA[{describe(lyr['title'], props)}]]></description>"
                             f"<styleUrl>#{sids[col]}</styleUrl>"
                             f"{gk}</Placemark>")

            sub.append(f"<Folder><name>{escape(lyr['title'])}</name>"
                       f"<visibility>{vis}</visibility><open>0</open>"
                       f"<Snippet>{len(marks)} features</Snippet>{''.join(marks)}</Folder>")
            report.append((key, f"{len(marks)} feats" + (f" (-{dropped})" if dropped else ""),
                           "on" if vis else "off"))

        if sub:
            folders.append(f"<Folder><name>{escape(grp['title'])}</name>"
                           f"<visibility>1</visibility><open>1</open>{''.join(sub)}</Folder>")

    doc = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<kml xmlns="http://www.opengis.net/kml/2.2">\n<Document>\n'
           f"<name>{escape(DOC_NAME)}</name>\n"
           "<visibility>1</visibility>\n<open>1</open>\n"
           f"<description><![CDATA[<div style=\"{BALLOON_CSS}\">{escape(DISCLAIMER)}</div>]]></description>\n"
           f"<LookAt><longitude>{(west + east) / 2:.6f}</longitude><latitude>{(south + north) / 2:.6f}</latitude>"
           "<altitude>0</altitude><heading>0</heading><tilt>0</tilt><range>14000</range>"
           "<altitudeMode>clampToGround</altitudeMode></LookAt>\n"
           + "\n".join(styles) + "\n" + "\n".join(folders) +
           "\n</Document>\n</kml>\n")

    checked = assert_feature_order(doc)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("doc.kml", doc)
        z.writestr("files/dot.png", circle_png())

    print(f"{'layer':<45} {'result':<12} default")
    print("-" * 74)
    for r in report:
        print(f"{r[0]:<45} {r[1]:<12} {r[2]}")
    if fallbacks:
        print("\nNo LAYER_STYLE entry (drew gray): " + ", ".join(fallbacks))
    on = [r[0] for r in report if r[2] == "on"]
    print(f"\nvisible on open ({len(on)}): " + ", ".join(on))
    print(f"KML element order verified on {checked} features")
    print(f"wrote {OUT} ({os.path.getsize(OUT) / 1e6:.1f} MB, "
          f"{sum(1 for _ in styles)} styles, doc.kml {len(doc) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
