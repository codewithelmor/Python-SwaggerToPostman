#!/usr/bin/env python3
"""
swagger_to_postman.py - Convert a Swagger / OpenAPI JSON file into a
Postman Collection (v2.1) JSON file.

The output file is named from the spec's info block:

    {info.title}_{info.version}.postman_collection.json
    e.g.  WebApplication1_API_v1.postman_collection.json

Usage:
    python swagger_to_postman.py swagger.json
    python swagger_to_postman.py swagger.json -o ./out --base-url https://localhost:5001

Only the Python standard library is used (Python 3.8+).
"""
import argparse
import json
import re
import sys
import uuid
from pathlib import Path

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
SCHEMA_URL = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"

STRING_FORMATS = {
    "date": "2024-01-01",
    "date-time": "2024-01-01T00:00:00Z",
    "time": "12:00:00",
    "uuid": "00000000-0000-0000-0000-000000000000",
    "email": "user@example.com",
    "uri": "https://example.com",
    "url": "https://example.com",
    "hostname": "example.com",
    "ipv4": "127.0.0.1",
    "ipv6": "::1",
    "byte": "c3RyaW5n",
}


# --------------------------------------------------------------------------- #
# Spec helpers
# --------------------------------------------------------------------------- #
def load_spec(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        sys.exit(f"Error: file not found: {path}")
    except json.JSONDecodeError as e:
        sys.exit(f"Error: {path} is not valid JSON ({e})")


def safe_name(text):
    """Make a string safe to use in a file name."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(text).strip()).strip("_") or "api"


def resolve(spec, node):
    """Follow local $ref pointers ('#/components/schemas/X')."""
    seen = set()
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if ref in seen or not ref.startswith("#/"):
            return {}
        seen.add(ref)
        target = spec
        try:
            for part in ref[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            return {}
        node = target
    return node if isinstance(node, dict) else {}


def example_value(spec, schema, stack=()):
    """Build an example value from a JSON schema."""
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref")
    if ref:
        if ref in stack:  # recursive schema
            return None
        stack = stack + (ref,)
        schema = resolve(spec, schema)
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    if schema.get("enum"):
        return schema["enum"][0]
    if schema.get("allOf"):
        merged = {}
        for sub in schema["allOf"]:
            value = example_value(spec, sub, stack)
            if isinstance(value, dict):
                merged.update(value)
        return merged
    for key in ("oneOf", "anyOf"):
        if schema.get(key):
            return example_value(spec, schema[key][0], stack)

    stype = schema.get("type")
    if stype is None and "properties" in schema:
        stype = "object"
    if stype == "object":
        result = {}
        for name, prop in (schema.get("properties") or {}).items():
            if resolve(spec, prop).get("readOnly"):
                continue
            result[name] = example_value(spec, prop, stack)
        return result
    if stype == "array":
        item = example_value(spec, schema.get("items", {}), stack)
        return [] if item is None else [item]
    if stype == "string":
        return STRING_FORMATS.get(schema.get("format"), "string")
    if stype == "integer":
        return schema.get("minimum", 0)
    if stype == "number":
        return schema.get("minimum", 0.0)
    if stype == "boolean":
        return False
    return None


def param_text(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(param_text(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def param_example(spec, p):
    if "example" in p:
        return p["example"]
    if p.get("examples"):
        first = resolve(spec, next(iter(p["examples"].values())))
        if "value" in first:
            return first["value"]
    return example_value(spec, p.get("schema", p))  # Swagger 2 keeps the type on p


def collect_params(spec, path_item, op):
    merged = {}
    for raw in (path_item.get("parameters") or []) + (op.get("parameters") or []):
        p = resolve(spec, raw)
        if "name" in p and "in" in p:
            merged[(p["name"], p["in"])] = p  # operation level overrides path level
    return list(merged.values())


def iter_operations(spec):
    for path, item in (spec.get("paths") or {}).items():
        item = resolve(spec, item)
        for method in HTTP_METHODS:
            op = item.get(method)
            if isinstance(op, dict):
                yield path, method, op, collect_params(spec, item, op)


def get_base_url(spec):
    servers = spec.get("servers")
    if servers and isinstance(servers[0], dict):
        url = servers[0].get("url", "/")
        for name, var in (servers[0].get("variables") or {}).items():
            url = url.replace("{" + name + "}", str(var.get("default", "")))
        if url.startswith("/"):
            url = "http://localhost" + url
        return url.rstrip("/")
    if spec.get("host"):  # Swagger 2.0
        scheme = (spec.get("schemes") or ["https"])[0]
        return (scheme + "://" + spec["host"] + spec.get("basePath", "")).rstrip("/")
    return "http://localhost"


def pick_content_type(content):
    if "application/json" in content:
        return "application/json"
    for ctype in content:
        if "json" in ctype:
            return ctype
    return next(iter(content))


def is_file(schema):
    return schema.get("format") == "binary" or schema.get("type") == "file"


def get_body(spec, op, params):
    """Return {'content_type', 'example', 'files'} or None."""
    if op.get("requestBody"):
        rb = resolve(spec, op["requestBody"])
        content = rb.get("content") or {}
        if content:
            ctype = pick_content_type(content)
            media = content[ctype] or {}
            schema = resolve(spec, media.get("schema", {}))
            if "example" in media:
                example = media["example"]
            elif media.get("examples"):
                first = resolve(spec, next(iter(media["examples"].values())))
                example = first.get("value")
            else:
                example = example_value(spec, media.get("schema", {}))
            files = {n for n, s in (schema.get("properties") or {}).items()
                     if is_file(resolve(spec, s))}
            return {"content_type": ctype, "example": example, "files": files}
        return None

    consumes = op.get("consumes") or spec.get("consumes") or []
    body_param = next((p for p in params if p.get("in") == "body"), None)
    if body_param:  # Swagger 2.0
        return {"content_type": consumes[0] if consumes else "application/json",
                "example": example_value(spec, body_param.get("schema", {})),
                "files": set()}
    form = [p for p in params if p.get("in") == "formData"]
    if form:  # Swagger 2.0
        files = {p["name"] for p in form if p.get("type") == "file"}
        multipart = bool(files) or "multipart/form-data" in consumes
        return {"content_type": "multipart/form-data" if multipart
                else "application/x-www-form-urlencoded",
                "example": {p["name"]: example_value(spec, p) for p in form},
                "files": files}
    return None


def get_accept(spec, op):
    types = []
    for code, resp in (op.get("responses") or {}).items():
        if str(code).startswith("2") or code == "default":
            types += list((resolve(spec, resp).get("content") or {}).keys())
    if not types:
        types = list(op.get("produces") or spec.get("produces") or [])
    if not types:
        return None
    return "application/json" if "application/json" in types else types[0]


def get_auth(spec, op):
    """Return the first applicable security scheme as a simple dict, or None."""
    reqs = op["security"] if "security" in op else spec.get("security")
    if not reqs:
        return None
    schemes = (spec.get("components") or {}).get("securitySchemes") \
        or spec.get("securityDefinitions") or {}
    for req in reqs:
        for name in req:
            s = resolve(spec, schemes.get(name))
            stype = s.get("type")
            if stype == "http" and s.get("scheme", "").lower() == "basic" or stype == "basic":
                return {"type": "basic"}
            if stype in ("http", "oauth2", "openIdConnect"):
                return {"type": "bearer"}
            if stype == "apiKey":
                return {"type": "apikey", "in": s.get("in", "header"),
                        "name": s.get("name", "X-API-Key")}
    return None


# --------------------------------------------------------------------------- #
# Postman generation
# --------------------------------------------------------------------------- #
def kv(key, value):
    return {"key": key, "value": value, "type": "string"}


def build_auth(auth, variables):
    if auth["type"] == "bearer":
        variables.setdefault("token", "")
        return {"type": "bearer", "bearer": [kv("token", "{{token}}")]}
    if auth["type"] == "basic":
        variables.setdefault("username", "")
        variables.setdefault("password", "")
        return {"type": "basic",
                "basic": [kv("username", "{{username}}"), kv("password", "{{password}}")]}
    variables.setdefault("apiKey", "")
    return {"type": "apikey",
            "apikey": [kv("key", auth["name"]), kv("value", "{{apiKey}}"), kv("in", auth["in"])]}


def build_body(body, headers):
    ctype, example = body["content_type"], body["example"]
    fields = example if isinstance(example, dict) else {}
    if "json" in ctype:
        headers.append({"key": "Content-Type", "value": ctype})
        return {"mode": "raw",
                "raw": json.dumps(example, indent=2, ensure_ascii=False),
                "options": {"raw": {"language": "json"}}}
    if ctype == "application/x-www-form-urlencoded":
        return {"mode": "urlencoded",
                "urlencoded": [{"key": k, "value": param_text(v), "type": "text"}
                               for k, v in fields.items()]}
    if ctype.startswith("multipart/"):
        formdata = []
        for k, v in fields.items():
            if k in body["files"]:
                formdata.append({"key": k, "type": "file", "src": []})
            else:
                formdata.append({"key": k, "value": param_text(v), "type": "text"})
        return {"mode": "formdata", "formdata": formdata}
    headers.append({"key": "Content-Type", "value": ctype})
    raw = example if isinstance(example, str) else json.dumps(example, ensure_ascii=False)
    return {"mode": "raw", "raw": raw}


def build_item(spec, path, method, op, params, variables):
    name = (op.get("summary") or op.get("operationId")
            or f"{method.upper()} {path}").strip().splitlines()[0]

    # URL
    postman_path = re.sub(r"\{([^}]+)\}", r":\1", path)
    segments = [s for s in postman_path.strip("/").split("/") if s]

    path_vars = []
    declared = {p["name"]: p for p in params if p["in"] == "path"}
    for pname in re.findall(r"\{([^}]+)\}", path):
        p = declared.get(pname, {})
        entry = {"key": pname, "value": param_text(param_example(spec, p)) or "1"}
        if p.get("description"):
            entry["description"] = p["description"]
        path_vars.append(entry)

    query = []
    for p in params:
        if p["in"] == "query":
            entry = {"key": p["name"], "value": param_text(param_example(spec, p))}
            if not p.get("required"):
                entry["disabled"] = True
            if p.get("description"):
                entry["description"] = p["description"]
            query.append(entry)

    # Headers
    headers = []
    accept = get_accept(spec, op)
    if accept:
        headers.append({"key": "Accept", "value": accept})
    for p in params:
        if p["in"] == "header":
            headers.append({"key": p["name"], "value": param_text(param_example(spec, p))})

    request = {"method": method.upper(), "header": headers}
    if op.get("description"):
        request["description"] = op["description"]

    # Auth: an explicit "security: []" on the operation means "no auth".
    auth = get_auth(spec, op)
    if auth:
        request["auth"] = build_auth(auth, variables)
    elif "security" in op and not op["security"]:
        request["auth"] = {"type": "noauth"}

    # Body
    body = get_body(spec, op, params)
    if body:
        request["body"] = build_body(body, headers)

    raw_url = "{{baseUrl}}" + postman_path
    enabled = [f"{q['key']}={q['value']}" for q in query if not q.get("disabled")]
    if enabled:
        raw_url += "?" + "&".join(enabled)
    url = {"raw": raw_url, "host": ["{{baseUrl}}"], "path": segments}
    if query:
        url["query"] = query
    if path_vars:
        url["variable"] = path_vars
    request["url"] = url

    return {"name": name, "request": request, "response": []}


def build_collection(spec, base_url):
    info = spec.get("info", {})
    title = info.get("title", "API")
    version = str(info.get("version", ""))
    variables = {"baseUrl": base_url}
    tag_desc = {t.get("name"): t.get("description")
                for t in spec.get("tags", []) if isinstance(t, dict)}

    root, folders = [], {}
    for path, method, op, params in iter_operations(spec):
        item = build_item(spec, path, method, op, params, variables)
        tags = op.get("tags") or []
        if tags:
            tag = str(tags[0])
            if tag not in folders:
                folder = {"name": tag, "item": []}
                if tag_desc.get(tag):
                    folder["description"] = tag_desc[tag]
                folders[tag] = folder
                root.append(folder)
            folders[tag]["item"].append(item)
        else:
            root.append(item)

    description = info.get("description") or ""
    description = (description + "\n\n" if description else "") + \
        f"Generated by swagger_to_postman.py from {title} {version}".strip()

    return {
        "info": {
            "_postman_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{title}:{version}")),
            "name": f"{title} {version}".strip(),
            "description": description,
            "version": version,
            "schema": SCHEMA_URL,
        },
        "item": root,
        "variable": [{"key": k, "value": v, "type": "string"} for k, v in variables.items()],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Convert Swagger/OpenAPI JSON into a Postman Collection v2.1 JSON file.")
    parser.add_argument("input", help="Path to the swagger/openapi JSON file")
    parser.add_argument("-o", "--output-dir", default=None,
                        help="Folder for the collection file (default: same folder as the input file)")
    parser.add_argument("--base-url",
                        help="Override the baseUrl variable (default: taken from the spec's servers/host)")
    args = parser.parse_args()

    spec = load_spec(args.input)
    if "paths" not in spec:
        sys.exit("Error: no 'paths' found - is this a Swagger/OpenAPI document?")

    info = spec.get("info", {})
    file_name = (f"{safe_name(info.get('title', 'api'))}_"
                 f"{safe_name(info.get('version', ''))}.postman_collection.json")
    out_dir = Path(args.output_dir) if args.output_dir else Path(args.input).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / file_name

    collection = build_collection(spec, (args.base_url or get_base_url(spec)).rstrip("/"))
    out_path.write_text(json.dumps(collection, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Written: {out_path}")


if __name__ == "__main__":
    main()
