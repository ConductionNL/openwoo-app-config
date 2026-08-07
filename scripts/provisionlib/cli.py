# SPDX-License-Identifier: EUPL-1.2
# role: library
#
# scripts/provisionlib/cli.py — argument parsing, secret resolution, dispatch.
#
# The CLI layer: resolve_* read credentials from flags / env / interactive
# prompt (never argv-only secrets, never logged), make_client builds the Client,
# cmd_* wire one subcommand each to a step, and build_parser/main tie it together.
"""CLI: argparse wiring, secret resolution, and subcommand dispatch."""

import argparse
import json
import sys

from .client import Client, ProvisionError
from .constants import API_KEY_HEADER, USER_MENU_NAME
from .helpers import config_slugs, load_config, log
from .steps import (
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
    sync_check,
    verify_import,
)

def _from_env(var, flag):
    """Read a non-empty value from env var `var`; raise if set-but-empty."""
    import os

    value = os.environ.get(var)
    if not value:
        raise ProvisionError(f"{flag} {var} is set but the env var is empty")
    return value


def resolve_user(args):
    """Admin user from --user, or an interactive prompt (default 'admin')."""
    if args.user:
        return args.user
    if sys.stdin.isatty():
        return input("Nextcloud admin user [admin]: ").strip() or "admin"
    raise ProvisionError("provide --user (or run interactively)")


def resolve_password(args, user):
    """Password from --password, --password-env, or an interactive prompt.

    When neither flag is given and we have a terminal, prompt with getpass so the
    secret never lands in argv, shell history or a file.
    """
    if args.password is not None:
        return args.password
    if args.password_env:
        return _from_env(args.password_env, "--password-env")
    import getpass

    if sys.stdin.isatty():
        value = getpass.getpass(f"App password for {user} @ {args.base}: ")
        if value:
            return value
    raise ProvisionError("provide --password, --password-env, or run interactively")


def resolve_apikey(args):
    """The real source key from --apikey / --apikey-env / an interactive prompt.

    Returns None (→ dummy test key) only when no flag is set and either there is
    no terminal or the operator leaves the prompt blank.
    """
    if getattr(args, "apikey", None) is not None:
        return args.apikey
    if getattr(args, "apikey_env", None):
        return _from_env(args.apikey_env, "--apikey-env")
    import getpass

    if sys.stdin.isatty():
        value = getpass.getpass("Source API key (blank = dummy test key): ")
        return value or None
    return None


def _prompt_optional(args, attr, label):
    """A non-secret per-tenant value from a flag or a blank-able prompt (else None)."""
    value = getattr(args, attr, None)
    if value:
        return value
    if sys.stdin.isatty():
        return input(f"{label} (blank = keep config default): ").strip() or None
    return None


def resolve_source_url(args):
    """Source URL (`location`) from --source-url or a prompt; None keeps config."""
    return _prompt_optional(args, "source_url", "Source URL")


def resolve_interface_id(args):
    """API-Interface-ID from --api-interface-id or a prompt; None keeps config."""
    return _prompt_optional(args, "api_interface_id", "API-Interface-ID")


def make_client(args):
    """Build a Client, resolving user + password (flags or interactive prompt)."""
    user = resolve_user(args)
    return Client(args.base, user, resolve_password(args, user),
                  host_header=getattr(args, "host_header", None))


def cmd_credentials(args):
    doc = load_config(args.config)
    apikey = resolve_apikey(args)
    source_url = resolve_source_url(args)
    interface_id = resolve_interface_id(args)
    client = make_client(args)
    log(f"provisioning credentials against {args.base}")
    count = provision_credentials(client, doc, apikey=apikey, source_url=source_url,
                                  interface_id=interface_id, header=args.header)
    log(f"CREDENTIALS PROVISIONED OK ({count} source(s))")
    return 0


def cmd_settings(args):
    client = make_client(args)
    organisation = {"auto_create_default_organisation": True}
    if args.default_organisation:
        organisation["default_organisation"] = args.default_organisation
    multitenancy = {
        "enabled": args.multitenancy,
        "defaultUserTenant": "",
        "defaultObjectTenant": "",
        "adminOverride": True,
    }
    log(f"provisioning settings against {args.base}")
    provision_settings(client, organisation, multitenancy)
    log("SETTINGS PROVISIONED OK (organisation + multitenancy)")
    return 0


def _settings_from_args(args):
    """Build the settings payloads from CLI args, or None when --skip-settings."""
    if args.skip_settings:
        return None
    organisation = {"auto_create_default_organisation": True}
    if args.default_organisation:
        organisation["default_organisation"] = args.default_organisation
    multitenancy = {
        "enabled": args.multitenancy,
        "defaultUserTenant": "",
        "defaultObjectTenant": "",
        "adminOverride": True,
    }
    return {"organisation": organisation, "multitenancy": multitenancy}


def cmd_all(args):
    doc = load_config(args.config)
    client = make_client(args)
    log(f"full provisioning against {args.base}")
    provision_all(
        client,
        doc,
        apikey=resolve_apikey(args),
        source_url=resolve_source_url(args),
        interface_id=resolve_interface_id(args),
        settings=_settings_from_args(args),
        oc_settings=not args.skip_oc_settings,
        do_import=not args.skip_import,
        force_import=args.force_import,
        catalog=not args.skip_catalog,
        delete_menu=not args.skip_delete_menu,
        menu_name=args.menu_name,
        do_credentials=not args.skip_credentials,
        job_user=args.job_user or client.user,
        run_syncs=args.run_syncs,
        sync_mode="test" if args.test else "run",
    )
    log("FULL PROVISIONING OK")
    return 0


def cmd_sync_run(args):
    doc = load_config(args.config)
    client = make_client(args)
    mode = "test" if args.test else "run"
    log(f"{mode}ning {len(config_slugs(doc, 'synchronizations'))} synchronization(s) on {args.base}")
    done = provision_sync_run(client, doc, mode=mode)
    log(f"SYNC {mode.upper()} OK ({len(done)} synchronization(s))")
    return 0


def cmd_catalog(args):
    doc = load_config(args.config)
    client = make_client(args)
    log(f"provisioning catalog '{args.catalog_slug}' against {args.base}")
    provision_catalog(client, doc, catalog_slug=args.catalog_slug, target_register=args.register)
    log("CATALOG PROVISIONED OK")
    return 0


def cmd_delete_menu(args):
    client = make_client(args)
    log(f"deleting '{args.menu_name}' menu object on {args.base} (if present)")
    n = provision_delete_menu(client, name=args.menu_name)
    log(f"DELETE-MENU OK ({n} object(s) deleted)")
    return 0


def cmd_objects(args):
    client = make_client(args)
    with open(args.payload_file, encoding="utf-8") as fh:
        payload = json.load(fh)
    log(f"creating object in {args.register}/{args.schema} on {args.base}")
    obj = provision_object(client, args.register, args.schema, payload)
    log(f"OBJECT CREATED OK (id={obj.get('id') or obj.get('uuid')})")
    return 0


def cmd_import(args):
    doc = load_config(args.config)
    client = make_client(args)
    log(f"importing config into {args.base}")
    provision_import(client, doc, force=args.force)
    log("IMPORT OK")
    return 0


def cmd_authorization(args):
    doc = load_config(args.config)
    client = make_client(args)
    log(f"restoring schema authorization on {args.base}")
    n = provision_authorization(client, doc)
    log(f"AUTHORIZATION OK ({n} schema(s) patched)")
    return 0


def cmd_jobs(args):
    doc = load_config(args.config)
    client = make_client(args)
    job_user = args.job_user or client.user
    log(f"resolving job synchronizationIds on {args.base} (userId={job_user})")
    n = provision_jobs(client, doc, job_user=job_user)
    log(f"JOBS OK ({n} job(s) updated)")
    return 0


def cmd_syncs(args):
    doc = load_config(args.config)
    client = make_client(args)
    log(f"resolving synchronization references on {args.base}")
    n = provision_syncs(client, doc)
    log(f"SYNCS OK ({n} synchronization(s) resolved)")
    return 0


def cmd_rules(args):
    doc = load_config(args.config)
    client = make_client(args)
    log(f"resolving rule (fetch_file) source references on {args.base}")
    n = provision_rules(client, doc)
    log(f"RULES OK ({n} rule(s) resolved)")
    return 0


def cmd_oc_settings(args):
    client = make_client(args)
    log(f"provisioning OpenCatalogi settings against {args.base}")
    provision_oc_settings(client, register=args.register)
    log("OC-SETTINGS PROVISIONED OK")
    return 0


def cmd_verify_import(args):
    doc = load_config(args.config)
    client = make_client(args)
    log(f"verifying imported config slugs against {args.base}")
    report = verify_import(client, doc)
    failed = False
    for bucket, info in report.items():
        missing = info["missing"]
        if missing:
            failed = True
            log(f"  {bucket}: MISSING {len(missing)}/{info['expected']}: {missing}")
        else:
            log(f"  {bucket}: {info['expected']}/{info['expected']} present OK")
    if failed:
        raise ProvisionError("config entities missing on the tenant — import incomplete")
    log("IMPORT VERIFIED OK (all config slugs present)")
    return 0


def cmd_sync_check(args):
    doc = load_config(args.config)
    client = make_client(args)
    log(f"checking synchronization targets resolved on {args.base}")
    result = sync_check(client, doc)
    if result["dangling"]:
        for d in result["dangling"]:
            log(f"  DANGLING {d['slug']}: targetId={d['targetId']!r} (schema not resolved)")
        raise ProvisionError(
            f"{len(result['dangling'])} synchronization(s) have an unresolved target schema"
        )
    log(f"SYNC CHECK OK ({result['total']} syncs, all targets resolved)")
    return 0


def _add_connection_args(p, with_config=True):
    """Shared connection flags: base URL, user, and password (kept out of argv)."""
    p.add_argument("--base", required=True, help="instance base URL")
    p.add_argument("--user", default=None, help="admin user (prompted if omitted, default admin)")
    p.add_argument(
        "--password", default=None, help="password / app password (prefer --password-env)"
    )
    p.add_argument(
        "--password-env",
        default=None,
        help="read the password from this env var (kept out of argv)",
    )
    p.add_argument(
        "--host-header",
        default=None,
        help="override the Host header (a trusted_domain) — e.g. in-cluster against http://nextcloud:8080",
    )
    if with_config:
        p.add_argument(
            "--config",
            default="config/woo.configuration.json",
            help="config to drive the step (default: %(default)s)",
        )


def build_parser():
    parser = argparse.ArgumentParser(
        description="Post-import tenant provisioning steps over the API."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    cred = sub.add_parser(
        "credentials",
        help="set every config source's API-key header and assert it reflects",
    )
    _add_connection_args(cred)
    cred.add_argument(
        "--apikey", default=None, help="real API key to set (omit to write a dummy test key)"
    )
    cred.add_argument(
        "--apikey-env", default=None, help="read the real API key from this env var (never logged)"
    )
    cred.add_argument(
        "--header", default=API_KEY_HEADER, help="source configuration key to set (default: %(default)s)"
    )
    cred.add_argument("--source-url", default=None, help="source location URL (prompted if omitted; blank keeps config)")
    cred.add_argument("--api-interface-id", default=None, help="API-Interface-ID header (prompted if omitted; blank keeps config)")
    cred.set_defaults(func=cmd_credentials)

    st = sub.add_parser(
        "settings",
        help="PUT organisation + multitenancy settings and assert they reflect",
    )
    _add_connection_args(st, with_config=False)
    st.add_argument(
        "--default-organisation",
        default=None,
        help="org UUID to set (omit to rely on auto_create_default_organisation)",
    )
    st.add_argument(
        "--multitenancy",
        action="store_true",
        help="enable multitenancy (default: disabled)",
    )
    st.set_defaults(func=cmd_settings)

    im = sub.add_parser(
        "import",
        help="upload the config (skips if already present) and assert success",
    )
    _add_connection_args(im)
    im.add_argument("--force", action="store_true", help="re-upload even if every config slug is already present")
    im.set_defaults(func=cmd_import)

    au = sub.add_parser(
        "authorization",
        help="(repair) enforce schema authorization flags (e.g. inheritFromPublic) via the schema API",
    )
    _add_connection_args(au)
    au.set_defaults(func=cmd_authorization)

    jb = sub.add_parser(
        "jobs",
        help="resolve each job's synchronizationId (sync slug -> tenant numeric id) and assert",
    )
    _add_connection_args(jb)
    jb.add_argument("--job-user", default=None,
                    help="job userId to set on every job (default: the admin --user; workaround for Anonymous job runs)")
    jb.set_defaults(func=cmd_jobs)

    sy = sub.add_parser(
        "syncs",
        help="resolve each synchronization's slug refs (sourceId/mapping/actions/targetId -> tenant ids) and assert",
    )
    _add_connection_args(sy)
    sy.set_defaults(func=cmd_syncs)

    ru = sub.add_parser(
        "rules",
        help="resolve each fetch_file rule's source slug -> tenant numeric id and assert",
    )
    _add_connection_args(ru)
    ru.set_defaults(func=cmd_rules)

    ocs = sub.add_parser(
        "oc-settings",
        help="couple OpenCatalogi object types (catalog/listing/…) to their register + schema",
    )
    _add_connection_args(ocs, with_config=False)
    ocs.add_argument("--register", default="publication", help="OpenCatalogi base register slug (default: %(default)s)")
    ocs.set_defaults(func=cmd_oc_settings)

    vi = sub.add_parser(
        "verify-import",
        help="assert every config slug (registers/schemas/sources/syncs) is present on the tenant",
    )
    _add_connection_args(vi)
    vi.set_defaults(func=cmd_verify_import)

    sc = sub.add_parser(
        "sync-check",
        help="assert every config synchronization resolved its target schema (no dangling slug)",
    )
    _add_connection_args(sc)
    sc.set_defaults(func=cmd_sync_check)

    sr = sub.add_parser(
        "sync-run",
        help="POST run (or --test) for every config synchronization; real run fetches live data",
    )
    _add_connection_args(sr)
    sr.add_argument(
        "--test",
        action="store_true",
        help="use the /test dry-run endpoint instead of /run (no real fetch)",
    )
    sr.set_defaults(func=cmd_sync_run)

    ob = sub.add_parser(
        "objects",
        help="create one object in a register/schema from a JSON payload file",
    )
    _add_connection_args(ob, with_config=False)
    ob.add_argument("--register", required=True, help="target register (slug or id)")
    ob.add_argument("--schema", required=True, help="target schema (slug or id)")
    ob.add_argument("--payload-file", required=True, help="JSON file with the object body")
    ob.set_defaults(func=cmd_objects)

    cat = sub.add_parser(
        "catalog",
        help="point the OpenCatalogi catalog object at the WOO register + all its schemas",
    )
    _add_connection_args(cat)
    cat.add_argument("--catalog-slug", default="publications", help="catalog object slug (default: %(default)s)")
    cat.add_argument("--register", default="woo", help="register slug to link (default: %(default)s)")
    cat.set_defaults(func=cmd_catalog)

    dm = sub.add_parser(
        "delete-menu",
        help="delete the OpenCatalogi default 'User Menu' object (it does not belong in the WOO config)",
    )
    _add_connection_args(dm, with_config=False)
    dm.add_argument("--menu-name", default=USER_MENU_NAME,
                    help="menu object name/title/slug to match and delete (default: %(default)s)")
    dm.set_defaults(func=cmd_delete_menu)

    al = sub.add_parser(
        "all",
        help="full bring-up: settings -> oc-settings -> import -> verify-import -> catalog -> delete-menu -> credentials -> sync-refs -> sync-check -> jobs [-> sync-run]",
    )
    _add_connection_args(al)
    al.add_argument("--apikey", default=None, help="real API key for the source (omit for dummy)")
    al.add_argument("--apikey-env", default=None, help="read the real API key from this env var")
    al.add_argument("--source-url", default=None, help="source location URL (prompted if omitted; blank keeps config)")
    al.add_argument("--api-interface-id", default=None, help="API-Interface-ID header (prompted if omitted; blank keeps config)")
    al.add_argument("--default-organisation", default=None, help="org UUID (omit to rely on auto-create)")
    al.add_argument("--multitenancy", action="store_true", help="enable multitenancy (default: disabled)")
    al.add_argument("--skip-settings", action="store_true", help="do not touch settings")
    al.add_argument("--skip-import", action="store_true", help="skip the config import (assume already imported)")
    al.add_argument("--force-import", action="store_true", help="re-upload the config even if already present (for content changes)")
    al.add_argument("--skip-oc-settings", action="store_true", help="skip the OpenCatalogi register/schema coupling")
    al.add_argument("--skip-catalog", action="store_true", help="skip pointing the catalog at the WOO schemas")
    al.add_argument("--skip-delete-menu", action="store_true", help="do not delete the OpenCatalogi default 'User Menu' object")
    al.add_argument("--menu-name", default=USER_MENU_NAME, help="menu object name/title/slug to delete (default: %(default)s)")
    al.add_argument("--skip-credentials", action="store_true", help="skip source credentials (per-tenant source params set out-of-band, e.g. base-config-only Argo runs)")
    al.add_argument("--job-user", default=None,
                    help="job userId to set on every job (default: the admin --user; workaround for Anonymous job runs)")
    al.add_argument("--run-syncs", action="store_true", help="also run each synchronization at the end")
    al.add_argument("--test", action="store_true", help="with --run-syncs, use the /test dry-run endpoint")
    al.set_defaults(func=cmd_all)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ProvisionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
