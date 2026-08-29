# Data Sources — Topic 02

| Source | Type | Access | Priority |
|---|---|---|---|
| Danish Maritime Authority — dma.dk/safety-at-sea/navigational-information/download-data | Bulk historical AIS, Baltic | FREE, GOVERNMENT | **PRIMARY** |
| Copernicus / Sentinel-1 SAR (ESA/EU) | Satellite SAR; standard dark-vessel technique | Free, government | Cross-check |
| Live AIS feed (community aggregators) | Real-time AIS | **VERIFY TERMS BEFORE USE** | T2 live demo |
| MacBook camera | Live EO | Owned | T2 live demo |
| Singapore Maritime Dataset / SeaShips | Labelled vessel imagery | Academic — verify licence | Detector eval |

## Build order
Build against the Danish Maritime Authority historical bulk data FIRST. It is free,
governmental, Baltic-relevant, downloadable immediately, and has no rate limit or
uptime risk. A live feed is a demo upgrade, not a foundation.

## Licence discipline
Check and record the licence for every source before use. Cite all of them in the
pitch. Government open data is not automatically unrestricted.
---

## Recorded licences

Full text, consequences and the open action are in **`02_data/INVENTORY.md` §2 and §8**.
Summary, so nobody has to go looking before a commit:

| Source | Licence | Status |
|---|---|---|
| DMA bulk AIS | Danish act no. 596 of 24 June 2005 (re-use of public sector information). No warranty; **no combining with other datasets to identify individuals** without Danish DPA authorisation; **redistribution not addressed**; DMA may sell AIS commercially. | **RECORDED. In use.** |
| Copernicus / Sentinel-1 | Free, ESA/EU | Not used |
| Live AIS aggregator (T2) | **NOT READ** | **Blocks T2.** Nothing connects until this row is filled |
| **EEA coastline for analysis v3.0 (Mar 2017)** | **CC-BY 4.0**, copyright European Environment Agency, "no limitations to public access". Attribution REQUIRED. 1:100,000 MMU, native EPSG:3035. Lineage: EUHYDRO + GSHHG. | **RECORDED. Needed for the land-crossing check** |
| MacBook camera | Owned | Available |
| SeaShips / Singapore Maritime | Academic, unverified | Not used |

**"Free to download" is not "openly licensed".** DMA's silence on redistribution means
committing raw AIS rows to a public repo takes an unforced position on terms that were
never granted. Lane A's ruling: **only pseudonymised golden windows are committed**
(`INVENTORY.md` §6). If a pitch slide is going to claim an open licence, get it from
DMA in writing first.

**The EEA coastline is the only dataset here with explicit, permissive reuse terms.**
Download: https://sdi.eea.europa.eu/data/9faa6ea1-372a-4826-a3c7-fb5b05e31c52 — unlike the
DMA AIS it can be cited on a slide without hedging, provided the EEA is credited. Do not
substitute a coarse coastline: see `02_data/INVENTORY.md` §4.11 for why a 1:110m dataset
would flag every vessel near Rødbyhavn and Puttgarden as sailing overland.

---

## Code and model licences

Recorded 2026-08-29. This register previously covered DATA only, and applied its own
rule — *"check and record the licence for every source before use"* — rigorously to
datasets and not at all to code and model weights. The model weights are the one place
an obligation reaches the whole console, so that is the gap with teeth.

| Component | Licence | Status |
|---|---|---|
| **Ultralytics YOLOv8 (`ultralytics`) + `yolov8n.pt` COCO-pretrained weights** | **AGPL-3.0-or-later**, unless an Ultralytics Enterprise Licence is held. AGPL §13 extends to network-interactive use, which includes serving the operator console over HTTP with a YOLO detector in the pipeline. Weights are stock COCO-pretrained; **COCO annotations are CC BY 4.0 (© COCO Consortium)** and COCO images remain under their individual Flickr terms. Nothing here is trained or fine-tuned. | **RECORDED. Optional dependency.** The primary sensor path `04_demo/pi_sensor.py` is classical CV — MOG2, no neural network, no ultralytics import. `03_src/eo_detector.py` and `edge/sensor_node.py` are the YOLO paths. If this project is ever released: release under AGPL-3.0, or ship the classical path only. |
| **Leaflet 1.9.4** (`03_src/web/index.html`, vendored to `/assets`) | **BSD-2-Clause**, © 2010–2024 Volodymyr Agafonkin, © 2010–2011 CloudMade. The copyright notice and disclaimer must be retained in any redistributed copy. | **RECORDED.** Vendoring the upstream `leaflet.js` unmodified satisfies this — **do not strip its header comment.** |
| Python dependencies | pyais MIT · pandas BSD-3 · geopandas BSD-3 · shapely BSD-3 · movingpandas BSD-3 · scikit-learn BSD-3 · pydantic MIT · folium MIT · loguru MIT · openai Apache-2.0 · pytest MIT · torch BSD-3 · picamera2 BSD-2 · fastapi MIT · uvicorn BSD-3 · opencv-python MIT/Apache-2.0 depending on build | RECORDED. All permissive. **`ultralytics` above is the only copyleft dependency in the tree.** |

### EEA coastline — attribution, discharged

CC-BY 4.0 attribution is a **condition of use**, not a note to self. The licence was
recorded in four places and the attribution itself was never actually displayed
anywhere a reader would see it — it lived only in a code docstring and two internal
registers. The plan was to satisfy it "on any slide that shows a land-crossing
finding", and `05_pitch/` is empty. This string discharges it, and belongs anywhere the
coastline is shown:

> Coastline: EEA coastline for analysis (polygon), v3.0 (March 2017).
> © European Environment Agency, licensed under CC BY 4.0
> (https://creativecommons.org/licenses/by/4.0/).
> Source: https://sdi.eea.europa.eu/data/9faa6ea1-372a-4826-a3c7-fb5b05e31c52
> Unmodified except for reprojection from EPSG:3035 and a 250 m inward erosion buffer.

The "except for" clause is required: CC-BY 4.0 §3(a)(1)(B) requires indicating
modifications, and this project performs both.

**Obligation status:** `CROSSES_LAND` has never run and the coastline is not on disk,
so the data is not currently in use. The obligation attaches the moment it is
downloaded and a land-crossing finding is shown.
