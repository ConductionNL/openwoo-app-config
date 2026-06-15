# SPDX-License-Identifier: EUPL-1.2
# role: library
#
# scripts/provisionlib/constants.py — API paths and provisioning defaults.
#
# Pure literal constants (no logic, no imports), shared across the lib. Kept in
# one place so a reader can audit every endpoint and default the provisioner
# touches in a single scan.
"""API endpoint paths and provisioning default constants."""

# The xxllnc / OpenWoo source authenticates with an API key sent as a request
# header; OpenConnector stores per-source headers as dotted keys inside the
# source `configuration` object (e.g. "headers.API-KEY"). The real key is never
# committed — it is injected here at provision time from --apikey / --apikey-env
# (a K8s secret / ESO in prod). The config ships an empty placeholder.
API_KEY_HEADER = "headers.API-KEY"

# Dummy apikey marker. Clearly not a real secret; safe to log. When no real key
# is supplied (the CI / local functional test), the credentials step writes
# "<prefix><slug>" so the assertion is per-source and deterministic.
DUMMY_APIKEY_PREFIX = "DUMMY-PROVISION-TEST-"


SOURCES_PATH = "/index.php/apps/openconnector/api/sources"
SYNCS_PATH = "/index.php/apps/openconnector/api/synchronizations"
JOBS_PATH = "/index.php/apps/openconnector/api/jobs"
REGISTERS_PATH = "/index.php/apps/openregister/api/registers"
SCHEMAS_PATH = "/index.php/apps/openregister/api/schemas"
MAPPINGS_PATH = "/index.php/apps/openconnector/api/mappings"
RULES_PATH = "/index.php/apps/openconnector/api/rules"

# Config bucket -> the tenant list endpoint that should hold its rows after
# import. Used by verify_import to compare config slugs against what is actually
# present on the tenant (a slug-level check the bulk row count cannot give).
# Buckets the config leaves empty are skipped, so `jobs` only kicks in once the
# config carries jobs (expected after the OpenRegister import hotfix).
VERIFY_BUCKETS = {
    "registers": REGISTERS_PATH,
    "schemas": SCHEMAS_PATH,
    "sources": SOURCES_PATH,
    "synchronizations": SYNCS_PATH,
    "jobs": JOBS_PATH,
}


OBJECTS_PATH = "/index.php/apps/openregister/api/objects"

MENU_REGISTER = "publication"
MENU_SCHEMA = "menu"
# OpenCatalogi auto-creates a default "User Menu" object on the publication
# register. It does not belong in the WOO config (no per-tenant menu is shipped),
# so the provisioner removes it. Match is case-insensitive against the object's
# name/title/slug; override with --menu-name if a tenant labels it differently.
USER_MENU_NAME = "User Menu"


IMPORT_PATH = "/index.php/apps/openregister/api/configurations/import"
# Authorization flag keys the `authorization` repair command enforces on the
# tenant. inheritFromPublic defaults to true; explicit false isolates
# publications per department (e.g. Almere). NOTE: OpenRegister 0.2.3's import
# rejected this key and silently dropped the schema; fixed in 1.0.3, which
# imports it natively. `import` now
# uploads the config as-is; `authorization` remains for explicit repair (e.g.
# flipping inheritFromPublic to false on an existing tenant).
AUTH_FLAG_KEYS = {"inheritFromPublic"}


CATALOG_REGISTER = "publication"
CATALOG_SCHEMA = "catalog"


SETTINGS_ORG_PATH = "/index.php/apps/openregister/api/settings/organisation"
SETTINGS_MT_PATH = "/index.php/apps/openregister/api/settings/multitenancy"
SETTINGS_RETENTION_PATH = "/index.php/apps/openregister/api/settings/retention"
SETTINGS_FILES_PATH = "/index.php/apps/openregister/api/settings/files"

# Trail policy applied to every tenant. A WOO sync creates many objects; audit +
# search trails add write/index overhead with little value for these published
# registers, so disable them. NOTE: governance trade-off — flip to True if a
# tenant needs the trails.
RETENTION_SETTINGS = {"auditTrailsEnabled": False, "searchTrailsEnabled": False}

# File text-extraction mode: "manual" (don't auto-extract on every file/object
# change — extract on demand). Partial PUT merges, so other file settings are kept.
# The /settings/files endpoint is OpenRegister 1.1.x+; on older tenants it is
# absent and the step is skipped (best-effort). Object text extraction
# (objectExtractionMode) lives in the `objectManagement` app value and is NOT
# settable via this API — set it in the OpenRegister UI (see docs).
FILE_SETTINGS = {"extractionMode": "manual"}


OC_SETTINGS_PATH = "/index.php/apps/opencatalogi/api/settings"
# OpenCatalogi object types; each is backed by a same-named schema in the
# OpenCatalogi base register (`publication`).
OC_OBJECT_TYPES = ["catalog", "listing", "organization", "theme", "page", "menu", "glossary"]


INTERFACE_ID_HEADER = "headers.API-Interface-ID"
