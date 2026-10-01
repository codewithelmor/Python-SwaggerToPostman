# swagger_to_postman.py

Converts a Swagger / OpenAPI JSON file into a **Postman Collection v2.1** JSON file that you can import straight into Postman (or Bruno, Insomnia, Thunder Client, etc.).

## Requirements

- Python 3.8+
- No third-party packages (standard library only)

## Usage

```bash
python swagger_to_postman.py <swagger.json> [-o OUTPUT_DIR] [--base-url URL]
```

| Argument | Description |
|---|---|
| `input` | Path to the Swagger / OpenAPI **JSON** file (OpenAPI 3.x and Swagger 2.0) |
| `-o`, `--output-dir` | Folder to write the collection to. Default: current folder |
| `--base-url` | Overrides the `baseUrl` collection variable. Default: taken from `servers[0].url` (or `host` + `basePath` for Swagger 2.0), falling back to `http://localhost` |

### Example

```bash
python swagger_to_postman.py swagger.json -o ./postman --base-url https://localhost:5001
```

Then in Postman: **Import** and select the generated file.

## Output file name

The file name is built from the `info` block of the spec:

```json
"info": {
  "title": "WebApplication1 API",
  "version": "v1"
}
```

```
{info.title}_{info.version}.postman_collection.json
->   WebApplication1_API_v1.postman_collection.json
```

Characters that are not letters, digits, `.`, `_` or `-` are replaced with `_`. The collection name inside Postman is `"{title} {version}"` (here `WebApplication1 API v1`).

## What gets converted

- **Folders**: operations are grouped into one folder per (first) tag. Operations with no tag go at the root. Tag descriptions are carried over.
- **Requests**: named by `summary` (or `operationId`, or `METHOD /path`), with the description copied across.
- **URL**: `{{baseUrl}}` + path. Path parameters use Postman's `:name` syntax and are listed as path variables with example values.
- **Query parameters**: all are listed with example values. Optional ones are added **disabled**, so tick the ones you want.
- **Headers**: `Accept` (from the 2xx response content types), header parameters and `Content-Type` for raw bodies.
- **Request bodies**: JSON (raw), `application/x-www-form-urlencoded` and `multipart/form-data` (file fields become file pickers). Example values are generated from the schema: explicit `example`/`default`/`enum` first, then format-aware placeholders (`date`, `uuid`, `email`, ...). `readOnly` properties are skipped, and `$ref`, `allOf`, `oneOf` / `anyOf` (first option) and recursive schemas are handled.
- **Auth** is set on each request, backed by collection variables:
  - bearer / OAuth2 / OpenID Connect -> `{{token}}`
  - basic -> `{{username}}` / `{{password}}`
  - API key -> `{{apiKey}}` (header or query, as declared)
  - operation-level `security: []` -> "No Auth"
- **Collection variables**: `baseUrl`, plus `token`, `username`, `password` or `apiKey` when used. Fill these in once in the collection's **Variables** tab.

## Limitations

- JSON specs only (convert YAML to JSON first).
- Only local `$ref` pointers (`#/...`) are resolved.
- Only the first security scheme of an operation is used.
- Cookie parameters and response examples are ignored.
