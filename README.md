# Adaptation Atlas API

REST API that exposes prioritized Adaptation Atlas datasets (climate, hazard, exposure
and adaptation) stored as Parquet files in AWS S3. Consumers query datasets through
business-oriented parameters (country, period, crop, severity...) without knowing the
storage layout. Responses are JSON by default and CSV on request.

Tabular datasets (Parquet) are queried with DuckDB; raster datasets (GeoTIFF) are
clipped, reprojected, resampled, converted and aggregated with rasterio/GDAL.

Built with **Python 3.10**, **FastAPI**, **DuckDB**, **rasterio** and **uv**.

## Architecture

The code follows a hexagonal (ports and adapters) architecture:

| Layer | Package | Responsibility |
|-------|---------|----------------|
| Domain | `adaptation_atlas_api.domain` | Dataset models, validation rules, use cases. No framework or storage dependency. |
| Ports | `adaptation_atlas_api.ports` | Abstract interfaces the domain depends on (catalog, storage, tabular reader, partition index, exporter). |
| Adapters | `adaptation_atlas_api.adapters` | Concrete implementations: YAML catalog, S3/local storage, DuckDB reader, rasterio reader, CSV/GeoTIFF/NetCDF exporters, plugin registry. |
| API | `adaptation_atlas_api.api` | FastAPI routers, request/response schemas, error handling, dependency injection. |
| Infrastructure | `adaptation_atlas_api.infrastructure` | Settings (`.env`) and logging. |

The dependency rule (`domain` never imports `adapters`, `api` or `infrastructure`) is
enforced with `import-linter`.

### Dataset catalog

Every published dataset is described by one YAML file under `catalog/<domain>/`. The file
declares its kind (`type: tabular` or `type: raster`), the storage path template (with Hive
partition placeholders, also usable inside file names such as
`{country}_seasonal-phenology.parquet`) and the accepted filters with their controlled
vocabularies. Tabular datasets add the fields returned in every record (or none, to return
every source column) and the sort order used for stable pagination; raster datasets add a
`raster:` block with the CRS, resolution, coverage, temporal resolution, how layer dates are
read from file names and band descriptions (`time_axis`), the variables with their units,
aggregation rules (`sum`, `mean`, `min`, `max`) and registered unit conversions, and the
transformations clients may request. Adding a dataset means adding a YAML file, not code:
every tabular dataset is served by the same records endpoint and every raster dataset by the
same raster endpoint.

To inspect a new dataset before registering it (schema, row count, distinct values):

```bash
uv run python scripts/inspect_dataset.py "domain=climate/type=observational/" --output inspection.json
uv run python scripts/inspect_raster.py "domain=climate/type=hazard-indices/" --output raster_inspection.json
```

## Requirements

- Python 3.10
- [uv](https://docs.astral.sh/uv/)
- Read access to the S3 bucket holding the datasets

## Installation

```bash
uv sync                 # creates .venv and installs runtime + dev dependencies
cp .env.example .env    # then fill in the AWS credentials and bucket
uv run pre-commit install
```

## Configuration

All settings are read from `.env` (see `.env.example` for the full list):

| Variable | Description |
|----------|-------------|
| `STORAGE_BACKEND` | `s3` (default) or `local` (development/tests). |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | Credentials used by DuckDB `httpfs`. Leave empty to use the AWS credential chain. |
| `AWS_REGION`, `S3_BUCKET`, `S3_ENDPOINT_URL` | Bucket location. |
| `LOCAL_STORAGE_ROOT` | Directory mirroring the bucket layout when `STORAGE_BACKEND=local`. |
| `CATALOG_PATH` | Directory with the dataset YAML files. |
| `PARTITION_CACHE_TTL_SECONDS` | Lifetime of the discovered partition index. |
| `DUCKDB_MEMORY_LIMIT`, `DUCKDB_THREADS`, `QUERY_TIMEOUT_SECONDS` | Query engine limits. |
| `MAX_SKIPPED_FILES` | Unreadable (corrupt or truncated) source files a single query may skip; `0` makes any unreadable file fail the query with 503. |
| `MAX_ROWS_PER_RESPONSE`, `DEFAULT_PAGE_SIZE` | Pagination limits. |
| `RASTER_MAX_PIXELS`, `RASTER_MAX_LAYERS`, `RASTER_MAX_POINTS` | Limits of one raster request (cells read or produced, time steps, sampled coordinates). |

## Running

```bash
uv run uvicorn adaptation_atlas_api.main:app --reload --port 8000
```

Interactive documentation: <http://localhost:8000/docs> (Swagger UI) and
<http://localhost:8000/redoc>.

## Endpoints (v1)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Service status. |
| GET | `/api/v1/datasets?domain=&type=` | List published datasets, optionally by domain and kind (`tabular` / `raster`). |
| GET | `/api/v1/datasets/{dataset_id}` | Filters, valid values, fields (tabular) or raster description and example request (raster), plus available partition combinations. |
| GET | `/api/v1/datasets/{dataset_id}/records` | Records of any tabular dataset (HU-001 hazard exposure and all others). |
| POST | `/api/v1/datasets/{dataset_id}/raster` | Spatial subset of any raster dataset (HU-003 scenarios, HU-004 observed climate). |

Calling the records endpoint on a raster dataset (or the raster endpoint on a tabular one)
returns `422 wrong_dataset_type` naming the right endpoint.

### Registered datasets

| Domain | Dataset ids |
|--------|-------------|
| hazard_exposure | `hazard_exposure` |
| climate | `climate_observed_admin`, `climate_hazard_change`, `climate_season_ensemble`, **raster:** `climate_observed_monthly`, `climate_hazard_indices` |
| exposure | `exposure_crop_livestock`, `exposure_population_worldpop`, `exposure_population_kenya_census`, `exposure_population_kenya_projections`, `exposure_kenya_flood_intersect` |
| impacts | `impacts_pest_disease`, `impacts_gyga_yield` |
| phenology | `phenology_seasonal` |
| socioeconomic | `socioeconomic_landuse_fao`, `socioeconomic_gdp_sector`, `socioeconomic_poverty_rates`, `socioeconomic_faostat`, `socioeconomic_acled_conflicts` |

Error code `filter_not_applicable` (422) is returned when a registered filter targets a
column that does not exist in the files selected by the other filters (for example
`severity` on the precipitation-change tables of `climate_hazard_change`).

### Passing dataset filters

Filters are dataset specific (see `GET /api/v1/datasets/{dataset_id}`) and can be sent in
two equivalent ways, which may be combined:

```bash
# As a JSON object in the `filters` parameter (convenient from Swagger UI)
curl -G "http://localhost:8000/api/v1/datasets/hazard_exposure/records" \
     --data-urlencode 'filters={"country":"KEN","period":"historic","crop":"maize"}'

# As ordinary query parameters (convenient from a browser, Python or R)
curl "http://localhost:8000/api/v1/datasets/hazard_exposure/records?country=KEN&period=historic&crop=maize"
```

Several values of the same filter are combined with OR and can be given as a JSON array
(`"crop":["maize","bean"]`), a repeated parameter (`crop=maize&crop=bean`) or a comma
separated list (`crop=maize,bean`). A filter can opt out with `multiple: false` in its
catalog definition.

### Example (HU-001, dataset `hazard_exposure`)

```bash
# Discover the filters and their valid values
curl "http://localhost:8000/api/v1/datasets/hazard_exposure"

# Maize exposure in Kenya, historic period, first sub-national level
curl "http://localhost:8000/api/v1/datasets/hazard_exposure/records?country=KEN&period=historic&crop=maize&admin_level=1"

# Same records as CSV
curl -o kenya_maize.csv "http://localhost:8000/api/v1/datasets/hazard_exposure/records?country=KEN&period=historic&crop=maize&admin_level=1&format=csv"

# Projected exposure from the NEX-GDDP ensemble mean, SSP2-4.5, 2041-2060
curl "http://localhost:8000/api/v1/datasets/hazard_exposure/records?country=ETH&period=2041-2060&scenario=ssp245&source=nex-gddp-cmip6&variable=vop_nominal-usd21&season_type=jagermeyr&model=ENSEMBLEmean&severity=moderate&crop=wheat"
```

Error responses share one schema:

```json
{
  "code": "invalid_filter_value",
  "message": "Invalid value 'XXX' for parameter 'country'.",
  "parameter": "country",
  "allowed_values": ["AGO", "BDI", "..."]
}
```

| Status | Meaning |
|--------|---------|
| 404 | Unknown dataset identifier. |
| 422 | Unsupported parameter, missing required parameter or value outside the vocabulary. |
| 503 | Source data unavailable (storage failure); internal paths are never exposed. |

### Raster datasets (HU-003 and HU-004)

Both user stories are served by one pipeline: resolve the layers of the series, clip or
sample them, reproject/resample, convert units and aggregate over time. Every step is
optional and driven by the request and the catalog entry, so observed series
(`climate_observed_monthly`, monthly GeoTIFFs) and scenario indices
(`climate_hazard_indices`, yearly GeoTIFFs with one band per month) share every function.

```bash
# Kenya, monthly precipitation of 2020 as a 12-band GeoTIFF in inches, UTM 37S at 10 km
curl -X POST "http://localhost:8000/api/v1/datasets/climate_observed_monthly/raster" \
     -H "Content-Type: application/json" -o kenya_ptot_2020.tif -D headers.txt \
     -d '{"filters":{"variable":"PTOT"},"start_date":"2020-01-01","end_date":"2020-12-31",
          "bbox":[33.9,-4.7,41.9,5.5],"unit":"in","target_crs":"EPSG:32737","resolution":10000,
          "resampling":"average","aggregation":{"type":"calendar_month"},"format":"geotiff"}'

# Crop-season precipitation at two points (planting 15 March, 120 days) as JSON
curl -X POST "http://localhost:8000/api/v1/datasets/climate_observed_monthly/raster" \
     -H "Content-Type: application/json" \
     -d '{"filters":{"variable":"PTOT"},"start_date":"2020-03-01","end_date":"2020-07-31",
          "points":[[36.82,-1.29],[35.28,0.51]],
          "aggregation":{"type":"crop_season","planting_date":"2020-03-15","duration_days":120},
          "format":"json"}'

# Mean number of dry days per month over 2041-2060 (SSP2-4.5, MPI-ESM1-2-HR) as NetCDF
curl -X POST "http://localhost:8000/api/v1/datasets/climate_hazard_indices/raster" \
     -H "Content-Type: application/json" -o ndd_ssp245.nc \
     -d '{"filters":{"scenario":"ssp245","period":"2041-2060","model":"MPI-ESM1-2-HR","variable":"ndd"},
          "geometry":{"type":"Polygon","coordinates":[[[36,-2],[38,-2],[38,0],[36,0],[36,-2]]]},
          "aggregation":{"type":"period"},"format":"netcdf"}'
```

* Geographic selection: exactly one of `geometry` (GeoJSON Polygon/MultiPolygon, WGS84),
  `bbox` (`[min_lon, min_lat, max_lon, max_lat]`) or `points` (`[[lon, lat], ...]`).
* Period: `start_date`/`end_date` (required for observed series; optional for scenarios,
  whose projected period is a filter).
* Aggregation: `none`, `period`, `calendar_month`, `fixed_window` (`start_date`,
  `window_days`), `crop_season` (`planting_date` plus `end_date` or `duration_days`) or
  `stages` (`windows: [{label, start, end}]`). The rule (`sum`/`mean`/`min`/`max`) comes
  from the variable's catalog entry. Windows that do not align with the source time steps
  (e.g. a 30-day window over monthly data) are prorated by overlapping days, and every
  output slice reports the fraction of its window covered by data (`coverage`).
* Units: only conversions registered in the catalog (`factor`/`offset`) are accepted.
* Output: `geotiff` (one band per slice, metadata in tags) or `netcdf` (`(time, y, x)`)
  for areas, `json` or `csv` (one row per point and slice) for points. The processing
  metadata (source layers, selection, grid, every transformation) is returned in the
  `X-Atlas-Metadata` header as JSON.
* Validation errors (422): `invalid_geometry`, `invalid_bbox`, `invalid_coordinates`,
  `invalid_date_range`, `period_outside_coverage`, `unsupported_combination`,
  `unsupported_crs`, `unsupported_resolution`, `unsupported_resampling`,
  `unsupported_unit`, `unsupported_aggregation`, `no_coverage`, `request_too_large`.

The units and aggregation rules of `climate_hazard_indices` were inferred from the index
names and must be confirmed by the data team before release.

### Partial responses (corrupt source files)

A truncated or corrupt Parquet object in the bucket (for example an interrupted upload
without the Parquet footer) is skipped instead of failing the whole query, up to
`MAX_SKIPPED_FILES` per request. The response then carries `metadata.partial: true` and
`metadata.skipped_files` with the partition path of each file left out, and the server
logs a warning. Clients should treat such responses as incomplete and the data team
should re-upload the listed files. To find every unreadable file under a prefix:

```bash
uv run python scripts/inspect_dataset.py --check-only "domain=hazard_exposure/"   # exit code 1 if any
```

## Development

```bash
uv run ruff check .            # lint
uv run ruff format .           # format
uv run mypy                    # typing
uv run lint-imports            # architecture rule
uv run pytest --cov=adaptation_atlas_api   # tests
```

Tests use small synthetic Parquet files generated on the fly and the local storage
backend, so no AWS access is needed to run them.
