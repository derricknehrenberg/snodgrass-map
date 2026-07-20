# snodgrass-map — Developer Guide

Shared stakeholder web map for the Snodgrass Recreation Emphasis Area (GMUG
Forest Plan MA 4.2). Live at https://derricknehrenberg.github.io/snodgrass-map/
via GitHub Pages (main branch, root). Project context lives in the OneDrive
Snodgrass folder's CLAUDE.md; this file covers the code.

## Architecture

```
Raw GIS (OneDrive, NOT in repo)
   └─ scripts/clip_layers.py     clips every cataloged layer to the boundary
        ├─ data/<group>/<layer>.geojson   EPSG:4326, 6-decimal precision, 2D
        ├─ data/layers_index.json         manifest index.html reads at runtime
        └─ downloads/snodgrass_clipped.gpkg   all layers, EPSG:26913 (UTM 13N)
   └─ scripts/build_offline.py   embeds data/ into downloads/snodgrass_map_offline.html
index.html                        single-file Leaflet app (no build step, no framework)
```

`index.html` fetches `data/layers_index.json` and lazy-loads each GeoJSON when
its checkbox is first ticked. If `window.__SNODGRASS_DATA__` exists (offline
build), it reads from that instead of fetching.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

RAW="/Users/derricknehrenberg/Library/CloudStorage/OneDrive-SharedLibraries-metrec/Recreation - Documents/Recreation Project Initiatives/2026/Snodgrass/Snodgrass Map Engine/Raw GIS"
.venv/bin/python scripts/clip_layers.py --raw-gis "$RAW"
.venv/bin/python scripts/build_offline.py

python3 -m http.server 8741   # local preview at http://localhost:8741/
```

After a data rebuild: commit + push (Pages redeploys automatically), and copy
`downloads/*` to OneDrive `Snodgrass Map Engine/Map Outputs/`.

## Adding a stakeholder / layer

1. Put raw data in `Raw GIS/<Stakeholder>/` on OneDrive.
2. In `scripts/clip_layers.py`: add `add(...)` entries in `build_catalog()`
   (source path, group key, output name, title, curated field list, default
   on/off) and the group key → display title in `GROUP_TITLES`.
3. In `index.html`: add a `LAYER_STYLE["<group>/<name>"]` entry (kinds:
   `poly-cat`, `poly-outline`, `line`, `point`) and popup field labels in
   `FIELD_LABELS`. Unstyled layers fall back to gray outline.
4. Rerun both scripts. Layers with zero features inside the boundary are
   skipped automatically and reported in the run summary.

## Conventions and gotchas

- Curate attributes in the catalog (`fields=`) — especially anything
  people-related. Parcels deliberately exclude owner mailing address and
  sale/valuation fields.
- Sources arrive in mixed CRS (UTM 13N, CO State Plane ft, odd WKT variants);
  geopandas reprojects from the embedded CRS — never assume, never hardcode.
- Gunnison County: shapefiles in `Pelletier/` are primary; `QField GeoPackages/`
  supplies Trail, Streams, lakes, PublicLands, Milemarkers (not in shapefile set).
- The clip boundary is `Raw GIS/Snodgrass GIS Boundary.geojson` (WGS84
  rectangle). If it changes, rerun everything.
- Leaflet + basemaps load from CDN/tile servers: index.html needs internet
  even in the offline build (data layers work offline, basemap doesn't).
- Keep the draft/not-a-survey-product disclaimer in the header and README.
- All committed data must be public record — the repo and Pages site are public.
