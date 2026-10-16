# Adaptation Atlas API

REST API that exposes prioritized Adaptation Atlas datasets (climate, hazard, exposure
and adaptation) stored as Parquet files in AWS S3. Consumers query datasets through
business-oriented parameters (country, period, crop, severity...) without knowing the
storage layout. Responses are JSON by default and CSV on request.

Built with **Python 3.10**, **FastAPI**, **DuckDB** and **uv**.

## Architecture

The code follows a hexagonal (ports and adapters) architecture:

| Layer | Package | Responsibility |
|-------|---------|----------------|
| Domain | `adaptation_atlas_api.domain` | Dataset models, validation rules, use cases. No framework or storage dependency. |
| Ports | `adaptation_atlas_api.ports` | Abstract interfaces the domain depends on (catalog, storage, tabular reader, partition index, exporter). |
| Adapters | `adaptation_atlas_api.adapters` | Concrete implementations: YAML catalog, S3/local storage, DuckDB reader, CSV exporter, plugin registry. |
| API | `adaptation_atlas_api.api` | FastAPI routers, request/response schemas, error handling, dependency injection. |
| Infrastructure | `adaptation_atlas_api.infrastructure` | Settings (`.env`) and logging. |

The dependency rule (`domain` never imports `adapters`, `api` or `infrastructure`) is
enforced with `import-linter`.

### Dataset catalog

Every published dataset is described by one YAML file in `catalog/`. The file declares the
storage path template (with Hive partition placeholders), the accepted filters with their
controlled vocabularies, the fields returned in every record and the sort order used for
stable pagination. Adding a dataset means adding a YAML file, not code.

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
| `MAX_ROWS_PER_RESPONSE`, `DEFAULT_PAGE_SIZE` | Pagination limits. |

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
| GET | `/api/v1/datasets` | List published datasets. |
| GET | `/api/v1/datasets/{dataset_id}` | Filters, valid values, fields and available partition combinations. |
| GET | `/api/v1/hazard-exposure/{dataset_id}` | HU-001: hazard exposure records for a territory. |

### HU-001 example

```bash
# Maize exposure in Kenya, historic period, first sub-national level
curl "http://localhost:8000/api/v1/hazard-exposure/hazard_exposure?country=KEN&period=historic&crop=maize&admin_level=1"

# Same records as CSV
curl -o kenya_maize.csv "http://localhost:8000/api/v1/hazard-exposure/hazard_exposure?country=KEN&period=historic&crop=maize&admin_level=1&format=csv"

# Projected exposure from the NEX-GDDP ensemble mean, SSP2-4.5, 2041-2060
curl "http://localhost:8000/api/v1/hazard-exposure/hazard_exposure?country=ETH&period=2041-2060&scenario=ssp245&source=nex-gddp-cmip6&variable=vop_nominal-usd21&season_type=jagermeyr&model=ENSEMBLEmean&severity=moderate&crop=wheat"
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
