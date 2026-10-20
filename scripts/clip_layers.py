#!/usr/bin/env python3
"""Clip stakeholder GIS layers to the Snodgrass Recreation Emphasis Area boundary.

Reads raw data from the OneDrive "Snodgrass Map Engine/Raw GIS" folder, clips every
layer to the Snodgrass GIS Boundary, and writes:
  - data/<stakeholder>/<layer>.geojson   (EPSG:4326, for the web map)
  - downloads/snodgrass_clipped.gpkg     (EPSG:26913 UTM 13N, one file for GIS users)
  - data/layers_index.json               (manifest the web map reads)

Usage:  python scripts/clip_layers.py [--raw-gis PATH]
"""
import argparse
import functools
import json
import os
import sys
import warnings
import xml.etree.ElementTree as ET
import zipfile

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely
from shapely.geometry import (GeometryCollection, LineString, MultiLineString,
                              MultiPoint, MultiPolygon, Point, Polygon)

warnings.filterwarnings("ignore")

DEFAULT_RAW = ("/Users/derricknehrenberg/Library/CloudStorage/"
               "OneDrive-SharedLibraries-metrec/Recreation - Documents/"
               "Recreation Project Initiatives/2026/Snodgrass/"
               "Snodgrass Map Engine/Raw GIS")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GPKG_CRS = "EPSG:26913"  # NAD83 / UTM 13N — matches the GMUG Forest Plan data

# Each entry: source path (relative to Raw GIS unless absolute), gdb layer name,
# stakeholder group, output name, display title, curated fields (None = keep all),
# and whether the web map turns it on by default.
def county_dir(raw):
    """The county folder has gone by a couple of names in OneDrive."""
    for name in ("Gunnison County", "Gunnison County Parcels"):
        p = os.path.join(raw, name)
        if os.path.isdir(p):
            return p
    return os.path.join(raw, "Gunnison County")  # fallback; layers will cache


# ---------- KML/KMZ loader ----------
# Driver-independent (zipfile + ElementTree) so KMZ folder structure survives:
# GDAL's LIBKML/KML drivers flatten or rename folders inconsistently across builds.
def _tag(el):
    return el.tag.rsplit("}", 1)[-1]


def _child(el, name):
    return next((c for c in el if _tag(c) == name), None)


def _name(el):
    n = _child(el, "name")
    return (n.text or "").strip() if n is not None else None


def _coords(el):
    """<coordinates> text -> [(lon, lat), ...]; altitude dropped."""
    out = []
    for tup in (el.text or "").split():
        parts = tup.split(",")
        if len(parts) >= 2:
            out.append((float(parts[0]), float(parts[1])))
    return out


def _kml_geom(el):
    t = _tag(el)
    if t in ("Point", "LineString", "LinearRing"):
        c = _coords(_child(el, "coordinates"))
        if t == "Point":
            return Point(c[0]) if c else None
        return LineString(c) if len(c) >= 2 else None
    if t == "Polygon":
        def rings(boundary):
            return [_coords(r.find(".//{*}coordinates"))
                    for b in el if _tag(b) == boundary for r in b if _tag(r) == "LinearRing"]
        outer = rings("outerBoundaryIs")
        return Polygon(outer[0], rings("innerBoundaryIs")) if outer and len(outer[0]) >= 3 else None
    if t == "MultiGeometry":
        parts = [g for g in (_kml_geom(c) for c in el) if g is not None]
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0]
        kinds = {g.geom_type for g in parts}
        if kinds == {"Point"}:
            return MultiPoint(parts)
        if kinds == {"LineString"}:
            return MultiLineString(parts)
        if kinds == {"Polygon"}:
            return MultiPolygon(parts)
        return GeometryCollection(parts)
    return None


@functools.lru_cache(maxsize=None)
def read_kmz(path, skip_folders=()):
    """Every Placemark in a KMZ/KML as a GeoDataFrame (EPSG:4326) with columns
    Name, Folder (immediate parent folder) and Group (top-level folder under the
    document's root folder). Folders named in skip_folders are never parsed."""
    if path.lower().endswith(".kmz"):
        with zipfile.ZipFile(path) as z:
            kml_name = next(n for n in z.namelist() if n.lower().endswith(".kml"))
            root = ET.fromstring(z.read(kml_name))
    else:
        root = ET.parse(path).getroot()
    doc = _child(root, "Document")
    doc = root if doc is None else doc
    # A single wrapping folder (common in exports) is the root, not a group.
    tops = [c for c in doc if _tag(c) == "Folder"]
    start, base = (tops[0], 1) if len(tops) == 1 else (doc, 0)
    rows = []

    def walk(el, folders):
        for c in el:
            t = _tag(c)
            if t in ("Folder", "Document"):
                name = _name(c) or ""
                if name not in skip_folders:
                    walk(c, folders + [name])
            elif t == "Placemark":
                geom = next((g for g in (_kml_geom(x) for x in c) if g is not None), None)
                if geom is None:
                    continue
                rows.append(dict(Name=_name(c),
                                 Folder=folders[-1] if folders else None,
                                 Group=folders[base] if len(folders) > base else None,
                                 geometry=geom))

    walk(start, [_name(start) or ""] if base else [])
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def build_catalog(raw, qfield):
    gdb = os.path.join(raw, "GMUG Forest Plan 2024",
                       "GMUG_ForestPlan_FinalDecision_DataFeatures_20240613.gdb")
    pel = os.path.join(county_dir(raw), "Pelletier")
    C = []

    def add(src, layer, group, name, title, fields=None, default=False, set_fields=None,
            crs=None, value_fixes=None, hold=False, loader=None):
        # loader: optional zero-arg callable returning a GeoDataFrame, used instead
        # of reading `src` with geopandas (src is then documentation only).
        C.append(dict(src=src, layer=layer, group=group, name=name,
                      title=title, fields=fields, default=default,
                      set_fields=set_fields, crs=crs, value_fixes=value_fixes,
                      hold=hold, loader=loader))

    # --- US Forest Service: GMUG Forest Plan 2024 (Final Decision) ---
    add(gdb, "GMUG_MAs_FinalDecision_20240613", "usfs_gmug",
        "management_areas", "Management Areas",
        ["Final_DECISION_ManagementArea", "Final_Decision_MA_Description",
         "Final_AltB_UniquePlaceName", "RoadlessAreaName", "Final_AltB_GIS_Acres"],
        default=True)
    add(gdb, "GMUG_ROS_Summer_FinalDecision_20240613", "usfs_gmug",
        "ros_summer", "Rec. Opportunity Spectrum — Summer",
        ["ROS_Summer_FinalDecision", "ROS_Summer_Description", "GIS_Acres"])
    add(gdb, "GMUG_ROS_Winter_FinalDecision_20240613", "usfs_gmug",
        "ros_winter", "Rec. Opportunity Spectrum — Winter",
        ["ROS_Winter_FinalDecision", "ROS_Winter_Description", "GIS_Acres"])
    add(gdb, "GMUG_SIO_FinalDecision_20240613", "usfs_gmug",
        "scenic_integrity", "Scenic Integrity Objectives",
        ["SIO_FinalDecision", "GIS_Acres"])
    add(gdb, "GMUG_TimberSuitability_FinalDecision_20240613", "usfs_gmug",
        "timber_suitability", "Timber Suitability",
        ["TimberSuitability", "COVER_TYPE", "Ecosystem", "GIS_ACRES"])
    for lyr, name, title in [
        ("GMUG_Overlay_WSR_FinalDecision_20240613", "overlay_wild_scenic_rivers", "Wild & Scenic River Overlay"),
        ("GMUG_Overlay_UtilityCorridors_FinalDecision_20240613", "overlay_utility_corridors", "Utility Corridor Overlay"),
        ("GMUG_Overlay_ScenicByways_FinalDecision_20240613", "overlay_scenic_byways", "Scenic Byway Overlay"),
        ("GMUG_Overlay_DesignatedTrails_FinalDecision_20240613", "overlay_designated_trails", "Designated Trail Overlay"),
        ("GMUG_Overlay_ConservationWatershedNetwork_FinalDecision_20240613", "overlay_conservation_watershed", "Conservation Watershed Overlay"),
    ]:
        add(gdb, lyr, "usfs_gmug", name, title)

    # --- Gunnison County: core shapefiles ---
    add(os.path.join(pel, "Taxparcelassessor.shp"), None, "gunnison_county",
        "parcels", "Tax Parcels",
        ["ParcelNumb", "OWNERNAME", "ACCOUNTTYP", "GISAcres", "PROPERTYLO", "SUBDIVISIO"])
    add(os.path.join(pel, "Road.shp"), None, "gunnison_county",
        "roads", "Roads", ["Name", "Label", "Type", "Length_mil"], default=True)
    add(os.path.join(pel, "Jurisdictions.shp"), None, "gunnison_county",
        "jurisdictions", "Jurisdictions", ["Name", "GISAcres"])
    add(os.path.join(pel, "Towns.shp"), None, "gunnison_county",
        "towns", "Towns", ["Name", "GISAcres"])
    add(os.path.join(pel, "Subdivision.shp"), None, "gunnison_county",
        "subdivisions", "Subdivisions", ["Notes", "GISAcres", "PlatDate"])
    add(os.path.join(pel, "Sections.shp"), None, "gunnison_county",
        "plss_sections", "PLSS Sections")
    # Address Points deliberately NOT published: the county layer carries full
    # street addresses, ZIP, parcel/account numbers and a `Vacant` flag marking
    # unoccupied homes. Removed 2026-08-11 at Derrick's request. Do not re-add.
    add(os.path.join(pel, "Driveway.shp"), None, "gunnison_county",
        "driveways", "Driveways")
    add(os.path.join(pel, "Exempt.shp"), None, "gunnison_county",
        "exempt_properties", "Exempt Properties")
    add(os.path.join(pel, "Taxdistrict.shp"), None, "gunnison_county",
        "tax_districts", "Tax Districts")
    add(os.path.join(pel, "VotingPrecincts.shp"), None, "gunnison_county",
        "voting_precincts", "Voting Precincts")

    # --- Gunnison County: QField-only GeoPackage layers ---
    add(os.path.join(qfield, "Trail.gpkg"), None, "gunnison_county",
        "trails", "Trails",
        ["name", "trail_num", "type", "surface", "manager", "length_mi_",
         "hiking", "horse", "bike", "motorcycle", "atv", "snowmobile", "ski",
         "dogs", "access", "seasonalit"], default=True)
    add(os.path.join(qfield, "Streams.gpkg"), None, "gunnison_county",
        "streams", "Streams", ["Name", "Miles"], default=True)
    add(os.path.join(qfield, "lakes.gpkg"), None, "gunnison_county",
        "lakes", "Lakes & Ponds", ["DOW_NAME", "NAME1", "FEATURE", "GISAcres"], default=True)
    add(os.path.join(qfield, "PublicLands.gpkg"), None, "gunnison_county",
        "public_lands", "Public Lands", ["Owner", "Group", "Name", "County"])
    add(os.path.join(qfield, "Milemarkers.gpkg"), None, "gunnison_county",
        "milemarkers", "Mile Markers", ["MilePost", "Roadname", "Type"])

    # --- Rocky Mountain Biological Laboratory ---
    add(os.path.join(raw, "RMBL", "RMBL_research_areas_2026.zip"), None, "rmbl",
        "research_areas", "Research Areas (2026)",
        fields=[], set_fields={"Name": "RMBL research area (2026)"}, default=True)

    # --- Mt. Crested Butte Water & Sanitation District ---
    # The proposed-reservoir shapefile arrived with no .prj. Its coordinates are
    # UTM 13N meters and land on the North Village parcels, so we assign 26913 to
    # match the GMUG data (NAD83 vs WGS84 differs ~1 m here — immaterial at this scale).
    # Rechecked 2026-10-20 after the district said its system is Web Mercator: the raw
    # coordinates are x≈329,100–330,000 / y≈4,308,500–4,309,100 (UTM-range; Web Mercator
    # would be x≈-11.9M / y≈4.7M) and the geometry areas match the Acres field under
    # 26913, so this file is UTM 13N regardless. Keep 26913.
    wsan = os.path.join(raw, "MtCBWaterSan")
    add(os.path.join(wsan, "Reservoirs Proposed", "reservoirs_proposed.shp"), None,
        "mtcb_water_san", "reservoirs_proposed", "Proposed Reservoirs",
        ["Name", "Acres"], default=True, crs="EPSG:26913",
        value_fixes={"Name": {"Proposed Cresent Lake Resevoir":
                              "Proposed Crescent Lake Reservoir"}})
    # Source water area + infrastructure: arrived 2026-10-20 from the district as
    # shapefiles in WGS 1984 Web Mercator (EPSG:3857, .prj included). The only
    # attribute is a meaningless Id, so a descriptive Name is set instead.
    sw = os.path.join(wsan, "Source Water", "Source Water for MetRec")
    add(os.path.join(sw, "Source Water.shp"), None,
        "mtcb_water_san", "source_water", "Source Water Area (general)",
        fields=[], set_fields={"Name": "Mt. CB Water & San source water area (general location)"},
        default=True)
    add(os.path.join(sw, "Source Water Infrastructure.shp"), None,
        "mtcb_water_san", "source_water_infrastructure", "Source Water Infrastructure",
        fields=[], set_fields={"Name": "Mt. CB Water & San source water line / infrastructure"},
        default=True)
    # HELD FROM PUBLICATION (Aug 2026): this is the full as-built Mt. CB distribution
    # system (mains, hydrant laterals, service lines with material and diameter), not a
    # proposed export alignment. The repo and Pages site are public, so per the project's
    # non-public-partner-data rule it stays out of every output until Mt. CB Water & San
    # confirms it may be posted publicly. Clear hold=True to publish.
    add(os.path.join(wsan, "Water_Export", "Water_Main.shp"), None,
        "mtcb_water_san", "water_mains", "Water Mains",
        # Created_By / Last_Edite are staff usernames; GlobalID is internal — all dropped.
        ["Facility_I", "Main_Type", "Water_Type", "System", "Material",
         "Diameter_I", "Install_Da", "Comment"], hold=True)

    # --- Otak: Snodgrass to Gothic trail feasibility (draft KMZ, 2026-10-19) ---
    # Attributes come from the KMZ folder tree (placemark names are mostly
    # auto-generated, descriptions empty). The KMZ's "Gunnison_Co_Parcels_20260728"
    # folder is never parsed: it carries owner mailing / sale / valuation fields,
    # and the map already has curated county parcels.
    otak = os.path.join(raw, "OTAK Feasibility KMZ", "23025-Snodgrass_to_Gothic_2026-10-19.kmz")
    otak_skip = ("Gunnison_Co_Parcels_20260728",)

    def otak_sel(pred):
        def load():
            g = read_kmz(otak, otak_skip)
            return g[pred(g)].copy()
        return load

    def otak_alignments():
        g = otak_sel(lambda g: g.Group.isin(["ALT 1", "ALT 2", "ALT 3"])
                     & (g.Folder != "Grading Limits")
                     & g.geom_type.isin(["LineString", "MultiLineString"]))()
        g["Alternative"] = g["Group"]
        g["Segment"] = g["Folder"].where(g["Folder"] != g["Group"], None)
        return g

    add(otak, None, "otak_feasibility", "alignments", "Trail Alignment Alternatives",
        # Placemark names are Google Earth auto-IDs ("kml_6"); only Alternative/Segment carry meaning.
        ["Alternative", "Segment"], default=True, loader=otak_alignments)
    add(otak, None, "otak_feasibility", "grading_limits", "ALT 2 Grading Limits",
        fields=[], set_fields={"Name": "ALT 2 grading limit (Otak, draft)"}, loader=otak_sel(lambda g: (g.Group == "ALT 2") & (g.Folder == "Grading Limits")))
    add(otak, None, "otak_feasibility", "east_river", "East River",
        ["Name"], loader=otak_sel(lambda g: (g.Folder == "East River and Wetlands")
                                  & g.geom_type.isin(["Polygon", "MultiPolygon"])))
    add(otak, None, "otak_feasibility", "wetlands", "Wetlands (delineated)",
        fields=[], set_fields={"Name": "Wetland boundary (Otak delineation, draft)"},
        loader=otak_sel(lambda g: (g.Group == "East River and Wetlands") & (g.Folder == "Wetlands")))
    add(otak, None, "otak_feasibility", "notes", "Field Notes",
        ["Name"], default=True, loader=otak_sel(lambda g: g.Group == "Notes"))
    return C

GROUP_TITLES = {
    "usfs_gmug": "US Forest Service — GMUG Forest Plan 2024",
    "gunnison_county": "Gunnison County",
    "rmbl": "Rocky Mountain Biological Laboratory",
    "mtcb_water_san": "Mt. Crested Butte Water & Sanitation",
    "otak_feasibility": "Otak — Snodgrass to Gothic Trail Feasibility (draft, Oct 2026)",
}
GROUP_ORDER = ("usfs_gmug", "gunnison_county", "rmbl", "mtcb_water_san", "otak_feasibility")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-gis", default=DEFAULT_RAW)
    ap.add_argument("--qfield", default=None,
                    help="Folder holding the extracted QField .gpkg files")
    args = ap.parse_args()

    qfield = args.qfield or os.path.join(county_dir(args.raw_gis), "QField GeoPackages")
    boundary_path = os.path.join(args.raw_gis, "Snodgrass GIS Boundary.geojson")
    boundary = gpd.read_file(boundary_path)  # EPSG:4326
    bnd_union = boundary.union_all()

    gpkg_out = os.path.join(REPO, "downloads", "snodgrass_clipped.gpkg")
    if os.path.exists(gpkg_out):
        os.remove(gpkg_out)

    manifest = {"groups": [], "generated_from": "clip_layers.py"}
    groups = {}
    report = []

    # Boundary itself goes in both outputs.
    os.makedirs(os.path.join(REPO, "data", "boundary"), exist_ok=True)
    pyogrio.write_dataframe(boundary, os.path.join(REPO, "data", "boundary", "snodgrass_boundary.geojson"),
                            driver="GeoJSON", COORDINATE_PRECISION=6)
    pyogrio.write_dataframe(boundary.to_crs(GPKG_CRS), gpkg_out,
                            layer="snodgrass_boundary", driver="GPKG")

    for item in build_catalog(args.raw_gis, qfield):
        label = f"{item['group']}/{item['name']}"
        out_dir = os.path.join(REPO, "data", item["group"])
        out_path = os.path.join(out_dir, item["name"] + ".geojson")

        if item.get("hold"):
            # Never publish, and scrub any copy an earlier run left behind — including
            # from the GeoPackage, which is rebuilt from scratch each run.
            if os.path.exists(out_path):
                os.remove(out_path)
            report.append((label, "HELD", "withheld from publication — see catalog note"))
            continue

        cached = False
        try:
            if item.get("loader"):
                gdf = item["loader"]()
            else:
                gdf = (gpd.read_file(item["src"], layer=item["layer"])
                       if item["layer"] else gpd.read_file(item["src"]))
        except Exception as e:
            # Raw source unavailable — keep the previously clipped output so the
            # layer doesn't silently vanish from the map.
            if os.path.exists(out_path):
                gdf = gpd.read_file(out_path)
                cached = True
            else:
                report.append((label, "READ ERROR", str(e)[:100]))
                continue

        if not cached:
            if gdf.crs is None and item.get("crs"):
                # Source shipped without a .prj; catalog declares the known CRS.
                gdf = gdf.set_crs(item["crs"])
            if gdf.crs is None:
                report.append((label, "SKIP", "no CRS defined"))
                continue
            mask = gpd.GeoSeries([bnd_union], crs="EPSG:4326").to_crs(gdf.crs).iloc[0]
            gdf = gdf[gdf.geometry.notna() & gdf.geometry.intersects(mask)]
            if len(gdf) == 0:
                report.append((label, "EMPTY", "no features inside boundary"))
                continue
            gdf = gdf.clip(mask)
            gdf = gdf[~gdf.geometry.is_empty]
            if len(gdf) == 0:
                report.append((label, "EMPTY", "clip produced no geometry"))
                continue

            if item["fields"] is not None:
                keep = [f for f in item["fields"] if f in gdf.columns]
                gdf = gdf[keep + ["geometry"]]
            else:
                gdf = gdf.drop(columns=[c for c in ("Shape_Length", "Shape_Area", "Shape_Leng")
                                        if c in gdf.columns])
            for k, v in (item.get("set_fields") or {}).items():
                gdf[k] = v
            # Display-only corrections to source values (e.g. misspelled labels).
            for col, mapping in (item.get("value_fixes") or {}).items():
                if col in gdf.columns:
                    gdf[col] = gdf[col].replace(mapping)
            gdf.geometry = shapely.force_2d(gdf.geometry)

            # GeoJSON (WGS84, for the web map)
            os.makedirs(out_dir, exist_ok=True)
            wgs = gdf.to_crs("EPSG:4326")
            pyogrio.write_dataframe(wgs, out_path, driver="GeoJSON", COORDINATE_PRECISION=6)

        # GeoPackage (projected, for GIS users) — written for fresh AND cached layers
        gdf.to_crs(GPKG_CRS).to_file(gpkg_out, layer=label.replace("/", "__"), driver="GPKG")

        size_kb = os.path.getsize(out_path) / 1024
        gtype = gdf.geom_type.mode()[0]
        status = f"{len(gdf)} feats" + (" (cached)" if cached else "")
        report.append((label, status, f"{gtype}, {size_kb:.0f} KB"))

        groups.setdefault(item["group"], []).append(dict(
            name=item["name"], title=item["title"],
            path=f"data/{item['group']}/{item['name']}.geojson",
            geometry=gtype, features=int(len(gdf)), default=item["default"]))

    for gkey in GROUP_ORDER:
        if gkey in groups:
            manifest["groups"].append(dict(key=gkey, title=GROUP_TITLES[gkey],
                                           layers=groups[gkey]))
    with open(os.path.join(REPO, "data", "layers_index.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"{'layer':<45} {'result':<12} detail")
    print("-" * 100)
    for r in report:
        print(f"{r[0]:<45} {r[1]:<12} {r[2]}")


if __name__ == "__main__":
    main()
