# WorldCereal embeddings viewer

Browser viewer for the per-S2-tile WorldCereal embedding COGs in
`s3://worldcereal-phase2-waw4-1-embeddings/worldcereal_embeddings/`.

- S2 grid outlines: **green** = on S3, **orange** = processed locally (not uploaded yet), **red** = to be processed.
- Click a green tile to stream its embeddings (RGB of any 3 of the 128 dimensions), the dequantized values or the scale.
- Click a pixel to read all 128 dimensions: uint8, scale and dequantized `(q − 128) · scale`, plotted and copyable.
- **3D explore** (PCA of a drawn box) and the **class sampler** (shared PCA across labelled boxes).
- Download buttons and https links for the embeddings, the scale and the scale `.aux.xml`.

## Files

| File                    | Purpose                                                                          |
|-------------------------|----------------------------------------------------------------------------------|
| `index.html`            | the viewer (single file: Leaflet, geotiff.js, proj4, Plotly)                     |
| `data/s2_grid.geojson`  | tile footprints + status; regenerate with `tools/build_grid.py`                  |
| `tools/build_grid.py`   | builds the grid from the processing index and checks S3 (HEAD, size match)       |

## Hosting

Static page (GitHub Pages). The browser reads the COGs directly from S3 with HTTP range requests, so the
bucket needs a CORS rule (same as the UK bucket): origin `*`, methods `GET`/`HEAD`, allowed header `Range`,
exposed headers `Content-Range`, `Content-Length`, `Accept-Ranges`, `ETag`.
Without it, viewing fails (downloads still work).

## Update tile status

```bash
python tools/build_grid.py          # reads /vitodata/.../europe-embeddings/index, HEADs S3
```
