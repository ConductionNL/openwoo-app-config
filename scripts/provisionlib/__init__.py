# SPDX-License-Identifier: EUPL-1.2
# role: library
#
# scripts/provisionlib/__init__.py — public API of the provisioning lib.
#
# Re-exports the names that make up the lib's surface so callers (and the unit
# tests) can do `import provisionlib as provision` and reach every step, helper,
# constant and the Client from one place. The thin `scripts/provision.py`
# entrypoint and the GUI/webgui shell out to the CLI; this package is the lib.
"""Tenant provisioning + validation lib (OpenRegister/OpenConnector/OpenCatalogi)."""

# Re-exported so `provision.urllib.request` resolves for callers/tests that patch
# the HTTP layer (the module object is shared with client.py).
import urllib.error
import urllib.request

from .client import Client, ProvisionError
from .constants import (
    API_KEY_HEADER,
    AUTH_FLAG_KEYS,
    CATALOG_REGISTER,
    CATALOG_SCHEMA,
    DUMMY_APIKEY_PREFIX,
    FILE_SETTINGS,
    IMPORT_PATH,
    INTERFACE_ID_HEADER,
    JOBS_PATH,
    MAPPINGS_PATH,
    MENU_REGISTER,
    MENU_SCHEMA,
    OBJECTS_PATH,
    OC_OBJECT_TYPES,
    OC_SETTINGS_PATH,
    REGISTERS_PATH,
    RETENTION_SETTINGS,
    RULES_PATH,
    SCHEMAS_PATH,
    SETTINGS_FILES_PATH,
    SETTINGS_MT_PATH,
    SETTINGS_ORG_PATH,
    SETTINGS_RETENTION_PATH,
    SOURCES_PATH,
    SYNCS_PATH,
    USER_MENU_NAME,
    VERIFY_BUCKETS,
)
from .helpers import (
    bucket_items,
    config_slugs,
    config_source_slugs,
    dummy_apikey,
    find_by_slug,
    load_config,
    log,
    merge_header,
    results_list,
    slug_to_id,
)
from .steps import (
    _menu_matches,
    provision_all,
    provision_authorization,
    provision_catalog,
    provision_credentials,
    provision_delete_menu,
    provision_import,
    provision_jobs,
    provision_object,
    provision_oc_settings,
    provision_rules,
    provision_settings,
    provision_sync_run,
    provision_syncs,
    put_settings_reflected,
    sync_check,
    target_schema_resolved,
    verify_import,
)
from .cli import (
    build_parser,
    main,
    make_client,
    resolve_apikey,
    resolve_interface_id,
    resolve_password,
    resolve_source_url,
    resolve_user,
)
