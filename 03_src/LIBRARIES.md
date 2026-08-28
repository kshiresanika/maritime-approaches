# LIBRARIES.md — the LIBRARY-FIRST register. ARCH-owned.

**Read this before writing any algorithm.** CLAUDE.md: never hand-roll NMEA/AIS
parsing, geodesy, trajectory resampling, or spatial joins. If nothing here fits, say
so in one line and justify it before you write the code.

The rule exists because hand-rolled geodesy at 02:00 does not crash — it silently
loses precision, and a position that is quietly 400 m wrong produces a confident
spoof verdict about the wrong hull.

---

## Use these, do not reinvent them

| Job | Library | Do NOT hand-roll |
|---|---|---|
| NMEA / AIS sentence decoding | `pyais` | AIS bit-unpacking, six-bit ASCII, message-type dispatch |
| Tabular AIS | `pandas` | CSV chunking, groupby, time indexing |
| Geometry predicates | `shapely` | point-in-polygon, distance-to-linestring, buffers |
| Spatial joins, CRS handling | `geopandas` | reprojection, cable-corridor joins, coastline intersects |
| Trajectory algebra | `movingpandas` | resampling, gap detection, speed/direction from a track |
| Clustering / thresholds | `scikit-learn` | k-means, DBSCAN, standard scalers |
| EO detection | `ultralytics` | anything YOLO |
| Frame capture, image ops | `opencv-python` | codec handling, colour conversion |
| Boundary types | `pydantic` | dataclasses with hand-written validation |
| Map output | `folium` | Leaflet HTML by hand |
| Logging | `loguru` | `print()` in library code |
| LLM calls | `openai` (pointed at Featherless) | raw `requests` against a chat endpoint |
| Tests | `pytest` | `if __name__ == "__main__"` smoke checks |

### Two things you must still write yourself

**Great-circle distance and bearing.** `geopandas`/`shapely` work in projected or
planar coordinates; for point-to-point range and bearing over the Baltic use
`pyproj.Geod` (ships with geopandas) — `Geod(ellps="WGS84").inv(lon1, lat1, lon2,
lat2)` returns forward azimuth, back azimuth and distance in metres. Do not write the
haversine formula. Do not use Euclidean distance on degrees.

**Signed shortest-arc angular difference.** One helper in `geometry.py`, owned by
lane B, used by everyone. `((a - b + 180) % 360) - 180`. 359° vs 1° is +2°, not 358°.
Every angular `Mismatch.delta` uses it.

---

## Environment

- **Python 3.12.10**, python.org framework build, Apple M4, macOS 26.5.2.
- **Virtualenv at `.venv/`**, created `--system-site-packages` so torch, ultralytics
  and cv2 come from the framework Python. `source .venv/bin/activate` — your prompt
  must show `(.venv)`.
- `.venv/` is **not portable and not committed**. There is no `requirements.txt`
  pinning yet. Teammates build their own env; the demo runs on Amol's Mac.

### Measured on the Mac (2026-08-27)

```
pandas 3.0.5   geopandas 1.1.4   ultralytics 8.4.130   torch 2.13.0   cv2 5.0.0
yolov8n @ 640x640, device="mps": 45.0 FPS (22.2 ms/frame)
camera: 1920x1080 via cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)
```

`pyais`, `shapely`, `movingpandas`, `pydantic` are installed but their versions were
never captured. Run the command below to close that gap.

### Install the rest and report every version

```sh
for p in scikit-learn folium pytest loguru openai; do python -m pip install "$p" || echo "INSTALL FAILED: $p"; done; python -c "import importlib.metadata as m
def v(n):
    try: return m.version(n)
    except Exception: return 'NOT_INSTALLED'
print('\n'.join(n+'=='+v(n) for n in 'pyais pandas geopandas shapely movingpandas scikit-learn ultralytics opencv-python pydantic folium pytest loguru openai'.split()))"
```

Every one of the thirteen has a macOS arm64 or pure-Python wheel on PyPI — **no
source build is required**. If you ever see `Building wheel for scikit-learn`, stop:
pip has fallen back to the sdist and you are about to wait out a long C build.

Watch pip's output for `Uninstalling numpy-...` or `Uninstalling pydantic-...`.
`openai` and `scikit-learn` can pull shared dependencies, and a silent downgrade
under a working torch/geo stack is the way this environment breaks mid-hackathon.

---

## Import convention — settled, do not invent your own

`03_src` starts with a digit, so it is **not a legal package name**: `import
03_src.contracts` can never work. The convention is flat imports with `03_src` on
`sys.path`:

```python
from contracts import AisTrack, EoContact, Verdict
```

`tests/conftest.py` does this for tests. `03_src/run_demo.py` (ARCH) does it for the
demo. Do not rename the directory — the numeric prefixes are what make the repo
legible to a judge in four minutes.

---

## Secrets — outside the repo, project-scoped

```sh
source ~/.config/edth-hamburg/edth-hamburg-2026.env
```

Defines **`EDTH_FEATHERLESS_KEY`**, `EDTH_FEATHERLESS_CHAT_MODEL`,
`EDTH_FEATHERLESS_VISION_MODEL`.

Names are project-scoped on purpose. A bare `FEATHERLESS_API_KEY` in `~/.zshrc` is a
single global slot — the last project sourced wins, and you get a 401 that looks like
a bad key rather than a collision. **A 401 from `report.py` almost always means you
did not source the env file.**

Nothing in this repo may contain a key. `.gitignore` covers `.env`, but the real
defence is that the file lives in `~/.config/edth-hamburg/`.

### The model

`Qwen/Qwen3-VL-30B-A3B-Instruct` serves **both** the rationale text and the vision
observation. One id, two call shapes, one failure mode.

Measured from `/v1/models`: `context_length` **131,072**, `max_completion_tokens`
32,768, `image_input: true`, `tool_use: true`, `concurrency_cost` 2, $0.15/M in,
$0.50/M out. Fallback `Qwen/Qwen2.5-VL-72B-Instruct` (32,768 context, cost 4, ~5×
price). `meta-llama/Llama-3.3-70B-Instruct` is **rejected** — no `image_input`, and
`is_gated: true`.

Base URL `https://api.featherless.ai/v1`, OpenAI-compatible, so use the `openai`
client with `base_url` set. Do not hand-roll HTTP.

> `constraints.md` item 12 says a 32K ceiling. That is the **plan** limit; our model
> serves 131K. Still chunk per-contact — for evidence traceability, so each rationale
> cites a bounded quotable window — not because context runs out. Keep chunking valid
> at 32K so the fallback stays usable.

---

## Model weights

`yolov8n.pt` (6.3 MB) is gitignored (`*.pt`). A fresh clone has none, and ultralytics
will silently try to fetch it over contested venue wifi on first run.
**AirDrop it to every teammate on arrival** and keep it at the repo root.

---

## Network gotchas that already cost time

- The DMA bucket name `aisdata.ais.dk` contains dots, so virtual-hosted-style S3
  breaks TLS against the `*.s3.eu-central-1.amazonaws.com` wildcard cert. **Always
  path-style**: `https://s3.eu-central-1.amazonaws.com/aisdata.ais.dk/`.
- Use `curl -sS`, never `-s`. `-s` hides errors as well as progress, so a TLS failure
  looks like an empty page.
- Any network result measured on venue wifi is provisional. A captive portal already
  produced one false "endpoint is down" diagnosis by hijacking port 80. HTTPS passes
  untouched.
