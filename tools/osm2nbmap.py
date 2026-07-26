#!/usr/bin/env python3
"""
osm2nbmap — convert an OpenStreetMap XML extract into the compact .nbmap binary
the Notebook OS Maps app renders. Build-time tool (runs on the host).

    osm2nbmap.py <in.osm> <out.nbmap> [name]

.nbmap layout (little-endian):
    magic   "NBMAP1\\n" (7 bytes)
    uint16  name length, then UTF-8 region name
    double  min_lat, min_lon, max_lat, max_lon      (bounding box)
    uint32  feature count
    per feature:
        uint8   category   (see CAT)
        uint8   flags      (bit0 = closed polygon)
        uint8   name length, name bytes (UTF-8, may be 0)
        uint16  point count
        points: point count × (int32 lat_e6, int32 lon_e6)   # degrees * 1e6
"""
import sys
import struct
import xml.etree.ElementTree as ET

# feature categories (kept small; the app styles each)
CAT = {"other": 0, "road_major": 1, "road_minor": 2, "path": 3, "water": 4,
       "waterway": 5, "building": 6, "green": 7, "landuse": 8, "rail": 9}

MAJOR = {"motorway", "trunk", "primary", "secondary", "motorway_link",
         "trunk_link", "primary_link", "secondary_link"}
MINOR = {"tertiary", "residential", "unclassified", "living_street",
         "service", "tertiary_link", "road"}
PATHS = {"footway", "path", "pedestrian", "steps", "cycleway", "track",
         "bridleway", "corridor"}
GREEN_LU = {"grass", "forest", "meadow", "recreation_ground", "village_green",
            "cemetery", "orchard", "farmland", "farmyard"}
GREEN_LEIS = {"park", "garden", "pitch", "playground", "nature_reserve",
              "golf_course"}


def classify(tags):
    hw = tags.get("highway")
    if hw:
        if hw in MAJOR:
            return "road_major"
        if hw in PATHS:
            return "path"
        return "road_minor"
    if tags.get("railway") in ("rail", "light_rail", "subway", "tram"):
        return "rail"
    if tags.get("natural") == "water" or tags.get("water"):
        return "water"
    if tags.get("waterway"):
        return "waterway"
    if tags.get("natural") == "wood":
        return "green"
    if tags.get("building"):
        return "building"
    lu = tags.get("landuse")
    if lu:
        return "green" if lu in GREEN_LU else "landuse"
    le = tags.get("leisure")
    if le:
        return "green" if le in GREEN_LEIS else "landuse"
    if tags.get("amenity") or tags.get("man_made"):
        return "landuse"
    return None


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    region = sys.argv[3] if len(sys.argv) > 3 else "Map"

    nodes = {}
    features = []
    minlat = minlon = 1e9
    maxlat = maxlon = -1e9

    # Stream-parse: nodes first (they precede ways in OSM output), then ways.
    cur = None
    for ev, el in ET.iterparse(src, events=("start", "end")):
        if ev == "start":
            if el.tag == "way":
                cur = {"nodes": [], "tags": {}}
            continue
        # end
        if el.tag == "node":
            try:
                nodes[el.get("id")] = (float(el.get("lat")),
                                       float(el.get("lon")))
            except (TypeError, ValueError):
                pass
            el.clear()
        elif el.tag == "nd" and cur is not None:
            cur["nodes"].append(el.get("ref"))
        elif el.tag == "tag" and cur is not None:
            cur["tags"][el.get("k")] = el.get("v")
        elif el.tag == "way":
            cat = classify(cur["tags"])
            if cat:
                pts = []
                for ref in cur["nodes"]:
                    p = nodes.get(ref)
                    if p:
                        pts.append(p)
                if len(pts) >= 2:
                    closed = (len(pts) >= 4 and cur["nodes"][0] ==
                              cur["nodes"][-1] and
                              cat in ("water", "building", "green", "landuse"))
                    name = cur["tags"].get("name", "")
                    features.append((CAT[cat], closed, name, pts))
                    for la, lo in pts:
                        minlat = min(minlat, la); maxlat = max(maxlat, la)
                        minlon = min(minlon, lo); maxlon = max(maxlon, lo)
            cur = None
            el.clear()

    if not features:
        print("no renderable features found")
        return 1

    # draw order: fills (landuse/green/water) under lines (roads) — sort by a
    # render-priority so the app can just draw in file order.
    order = {7: 0, 8: 1, 4: 2, 6: 3, 5: 4, 9: 5, 3: 6, 2: 7, 1: 8, 0: 1}
    features.sort(key=lambda f: order.get(f[0], 1))

    with open(dst, "wb") as fh:
        fh.write(b"NBMAP1\n")
        rn = region.encode("utf-8")[:65535]
        fh.write(struct.pack("<H", len(rn))); fh.write(rn)
        fh.write(struct.pack("<dddd", minlat, minlon, maxlat, maxlon))
        fh.write(struct.pack("<I", len(features)))
        for cat, closed, name, pts in features:
            nb = name.encode("utf-8")[:255]
            fh.write(struct.pack("<BBB", cat, 1 if closed else 0, len(nb)))
            fh.write(nb)
            fh.write(struct.pack("<H", min(len(pts), 65535)))
            for la, lo in pts[:65535]:
                fh.write(struct.pack("<ii", int(round(la * 1e6)),
                                     int(round(lo * 1e6))))
    import os
    print("wrote %s: %d features, %d nodes, %d bytes; bbox %.4f,%.4f,%.4f,%.4f"
          % (dst, len(features), len(nodes), os.path.getsize(dst),
             minlat, minlon, maxlat, maxlon))
    return 0


if __name__ == "__main__":
    sys.exit(main())
