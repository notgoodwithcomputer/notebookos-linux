# 024 — North America re-encode without footpaths

**Lane:** B · **Streams:** S10 · **Status:** CLOSED 2026-08-08

`NB_SKIP_PATHS=1` dropped the whole path category at classify time (both
passes + the store). Encode ran ~12h under setsid nohup (43,820s, peakRSS
15.33 GB, PID 37500) -> `mapwork/na-nopaths.nbm2`.

## Result, verified
- **86,102,258 features / 269,874 cells / 203,030 place labels / 2,277 MB.**
- Renderer (`mapwork/maprender_test.py`) loads it clean and streams: cells
  decoded on demand (25/35/132 for metro/region/wide), 52,314 features in the
  wide view. `view_wide.png` shows a correct dense map -- road network, water,
  green space, labels all placed.
- Major cities present with exact coords: New York (40.712,-74.006), Chicago
  (41.876,-87.624), Toronto (43.653,-79.384). (LA/Denver name-collide with
  smaller OSM towns -- a lookup artifact, not a data error.)

## The size finding -- an ISO-packaging DECISION, filed not made
Target was ~1.5 GB; actual is **2.28 GB**, only 15% under the old full pack
(2.7 GB, `release/north-america.nbm2`). Footpaths are ~40% of the FEATURE
COUNT but thin geometry, so ~15% of the SIZE -- the estimate conflated them.

2.28 GB pack + ~530 MB rootfs ~= **~2.8 GB ISO, which fits a 4 GB stick**, so
shipping as-is is viable. If a smaller image is wanted, the next lever is
dropping a feature class (landuse polygons, or minor water) -- a content
decision for the owner, recorded here rather than taken unilaterally. Old
2.7 GB pack retained until the ISO packaging is decided and built.
