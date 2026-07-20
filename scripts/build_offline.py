#!/usr/bin/env python3
"""Build a single self-contained HTML file with all layer data embedded.

The result (snodgrass_map_offline.html) opens by double-click with no server or
internet needed for the data (basemap tiles still need internet). Useful for
emailing or dropping in OneDrive.

Usage: python scripts/build_offline.py
"""
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def main():
    with open(os.path.join(REPO, "data", "layers_index.json")) as f:
        manifest = json.load(f)

    paths = ["data/boundary/snodgrass_boundary.geojson", "data/layers_index.json"]
    for g in manifest["groups"]:
        paths += [l["path"] for l in g["layers"]]

    blob = {}
    for p in paths:
        with open(os.path.join(REPO, p)) as f:
            blob[p] = json.load(f)

    with open(os.path.join(REPO, "index.html")) as f:
        html = f.read()

    inject = ("<script>window.__SNODGRASS_DATA__ = "
              + json.dumps(blob, separators=(",", ":"))
              + ";</script>\n<script src=")
    html = html.replace('<script src=', inject, 1)

    out = os.path.join(REPO, "downloads", "snodgrass_map_offline.html")
    with open(out, "w") as f:
        f.write(html)
    print(f"wrote {out} ({os.path.getsize(out)/1e6:.1f} MB)")

if __name__ == "__main__":
    main()
