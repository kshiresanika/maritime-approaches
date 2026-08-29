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
