# Snodgrass Recreation Emphasis Area — Shared Stakeholder Map

A common working map for partners coordinating recreation development in the
Snodgrass Recreation Emphasis Area (GMUG National Forest Management Area 4.2),
convened by the US Forest Service and coordinated by MetRec.

**Live map:** https://derricknehrenberg.github.io/snodgrass-map/

Every layer is clipped to a draft coordination boundary around Snodgrass
Mountain (the red dashed rectangle on the map). This is a planning aid, not a
survey product — verify positions against authoritative records before design,
engineering, or legal use.

## What's here

```
index.html                       interactive web map (GitHub Pages serves this)
data/
  boundary/                      the Snodgrass coordination boundary
  usfs_gmug/                     USFS — GMUG Forest Plan 2024 Final Decision layers
  gunnison_county/               Gunnison County GIS layers
  layers_index.json              manifest of groups/layers the map reads
downloads/
  snodgrass_clipped.gpkg         all clipped layers in one GeoPackage (EPSG:26913)
                                 — open directly in QGIS or ArcGIS Pro
  snodgrass_map_offline.html     single-file copy of the map with data embedded
scripts/
  clip_layers.py                 rebuilds data/ + the GeoPackage from raw sources
  build_offline.py               rebuilds the offline HTML from data/
```

## Stakeholder groups

One folder (and one map group) per stakeholder. Current and planned:

| Group | Status | Source |
|---|---|---|
| US Forest Service — GMUG Forest Plan 2024 | ✅ loaded | Forest Plan Final Decision geodatabase (2024-06-13) |
| Gunnison County | ✅ loaded | County GIS shapefiles + QField GeoPackages |
| Town of Mt. Crested Butte | ⏳ requested | — |
| Town of Crested Butte | ⏳ requested | — |
| RMBL | ⏳ requested | — |
| The Village at Mt. Crested Butte / NVA | ⏳ requested | — |
| Mt. CB Water & Sanitation District | ⏳ requested | — |
| MetRec | ⏳ planned | rec path priorities, survey-informed corridors |

## Contributing data (for partners)

Send GIS data for your organization's interests within (or near) the boundary
to derrick@gcmetrec.com. Preferred formats, best first:

1. **GeoPackage (.gpkg)** or **File Geodatabase (.gdb, zipped)**
2. **Shapefile** (zip the whole set: .shp/.shx/.dbf/.prj)
3. **KML/KMZ** (e.g., exported from Google Earth)

Any standard coordinate system is fine as long as a .prj/CRS is included — the
pipeline reprojects everything. Please include a one-line description per layer
and note anything that should *not* be shared publicly.

## Rebuilding

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/clip_layers.py --raw-gis "<path to Raw GIS folder>"
.venv/bin/python scripts/build_offline.py
```

Raw source data lives in the MetRec OneDrive under
`Recreation Project Initiatives/2026/Snodgrass/Snodgrass Map Engine/Raw GIS`
and is not stored in this repository.
