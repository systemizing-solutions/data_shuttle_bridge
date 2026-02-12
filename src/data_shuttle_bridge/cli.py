import os
import sys
import argparse
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlmodel import Session

from data_shuttle_bridge.sql.nodeid import ClientNodeManager
from data_shuttle_bridge.file_backup.cli import add_backup_commands
from data_shuttle_bridge.sql.versioning_models import (
    SchemaSet,
    SchemaVersion,
    MappingRule,
)
from data_shuttle_bridge.sql.schema_registry import SchemaRegistry
from data_shuttle_bridge.sql.view_builder import ConsolidationViewBuilder


def cmd_node_init(args: argparse.Namespace) -> int:
    server = args.server or os.environ.get("LOCALFIRST_SERVER")
    if not server:
        print(
            "Provide server via --server or LOCALFIRST_SERVER env var.", file=sys.stderr
        )
        return 2
    mgr = ClientNodeManager()
    node_id = mgr.ensure_node_id(server)
    print(f"device_key={mgr.device_key}")
    print(f"node_id={node_id}")
    print("Saved to ~/.localfirst_sync/config.json")
    return 0


def cmd_node_show(args: argparse.Namespace) -> int:
    mgr = ClientNodeManager()
    print(f"device_key={mgr.device_key}")
    print(f"node_id={mgr.node_id}")
    return 0


# ============================================================================
# Schema versioning commands
# ============================================================================


def _get_engine(db_url: str):
    """Get or create SQLAlchemy engine."""
    if not db_url:
        db_url = os.environ.get("SHUTTLE_DB_URL", "sqlite:///shuttle.db")
    return create_engine(db_url, echo=False)


def cmd_schema_create(args: argparse.Namespace) -> int:
    """Create a new schema set."""
    try:
        engine = _get_engine(args.db_url)
        registry = SchemaRegistry(engine)

        with Session(engine) as session:
            schema_set = registry.create_schema_set(
                session,
                key=args.key,
                name=args.name,
                description=args.description,
            )
            print(f"Created schema set: {schema_set.key} (id={schema_set.id})")
            return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_schema_list(args: argparse.Namespace) -> int:
    """List all schema sets."""
    try:
        engine = _get_engine(args.db_url)
        registry = SchemaRegistry(engine)

        with Session(engine) as session:
            sets = registry.list_schema_sets(session)
            if not sets:
                print("No schema sets found.")
                return 0

            print("Schema Sets:")
            for ss in sets:
                print(f"  {ss.key}: {ss.name}")
            return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_schema_add_version(args: argparse.Namespace) -> int:
    """Add a version to a schema set."""
    try:
        # Load schema from file
        schema_path = Path(args.file)
        if not schema_path.exists():
            print(f"Schema file not found: {args.file}", file=sys.stderr)
            return 1

        with open(schema_path) as f:
            schema_json = json.load(f)

        engine = _get_engine(args.db_url)
        registry = SchemaRegistry(engine)

        with Session(engine) as session:
            version = registry.add_schema_version(
                session,
                schema_set_key=args.key,
                version=args.version,
                schema_json=schema_json,
                parent_version=args.parent,
            )
            print(f"Created schema version {version.version} in {args.key}")
            print(f"Table: {version.table_name}")
            return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_schema_diff(args: argparse.Namespace) -> int:
    """Show diff between two schema versions."""
    try:
        engine = _get_engine(args.db_url)
        registry = SchemaRegistry(engine)

        with Session(engine) as session:
            diff = registry.get_schema_diff(
                session,
                schema_set_key=args.key,
                from_version=args.from_version,
                to_version=args.to_version,
            )

            if diff:
                print(json.dumps(diff, indent=2))
            else:
                print("No diff found")
            return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_data_ingest(args: argparse.Namespace) -> int:
    """Ingest JSON data into a schema version."""
    try:
        # Load payload from file or stdin
        if args.file:
            with open(args.file) as f:
                payload = json.load(f)
        else:
            payload = json.load(sys.stdin)

        engine = _get_engine(args.db_url)
        registry = SchemaRegistry(engine)

        with Session(engine) as session:
            row_id = registry.ingest_data(
                session,
                schema_set_key=args.key,
                version=args.version,
                payload=payload,
            )
            print(f"Inserted row with ID: {row_id}")
            return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_mapping_apply(args: argparse.Namespace) -> int:
    """Apply mapping rules to a schema version."""
    try:
        # Load mapping rules from file
        with open(args.file) as f:
            rules_data = json.load(f)

        engine = _get_engine(args.db_url)

        with Session(engine) as session:
            # Get schema version
            from data_shuttle_bridge.sql.versioning_models import (
                SchemaVersion,
                SchemaSet,
            )

            schema_set = session.exec(
                session.query(SchemaSet).filter(SchemaSet.key == args.key)
            ).first()

            if not schema_set:
                print(f"Schema set '{args.key}' not found", file=sys.stderr)
                return 1

            schema_version = session.exec(
                session.query(SchemaVersion).filter(
                    (SchemaVersion.schema_set_id == schema_set.id)
                    & (SchemaVersion.version == args.version)
                )
            ).first()

            if not schema_version:
                print(f"Version {args.version} not found", file=sys.stderr)
                return 1

            # Create or update mapping rule
            mapping_rule = MappingRule(
                schema_version_id=schema_version.id,
                rules_json=json.dumps(rules_data),
            )
            session.add(mapping_rule)
            session.commit()

            print(f"Applied mapping rules to {args.key} v{args.version}")
            return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_view_build(args: argparse.Namespace) -> int:
    """Build a consolidation view."""
    try:
        engine = _get_engine(args.db_url)

        # For now, simple implementation
        print(f"Building consolidation view '{args.name}' for {args.key}")
        print("(Full implementation coming with example scripts)")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="data-shuttle",
        description="Data synchronization and schema versioning tool",
    )
    parser.add_argument(
        "--db-url", default=None, help="Database URL (default: sqlite:///shuttle.db)"
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    # Node commands
    p_node = sub.add_parser("node", help="Node management commands")
    sub_node = p_node.add_subparsers(dest="node_cmd", required=True)

    p_node_init = sub_node.add_parser(
        "init", help="Lease or reuse a unique node_id from the server"
    )
    p_node_init.add_argument(
        "--server", help="Server base URL (e.g., http://127.0.0.1:5001)"
    )
    p_node_init.set_defaults(func=cmd_node_init)

    p_node_show = sub_node.add_parser("show", help="Show local device_key and node_id")
    p_node_show.set_defaults(func=cmd_node_show)

    # Backup commands
    add_backup_commands(sub)

    # Schema commands
    p_schema = sub.add_parser("schema", help="Schema versioning commands")
    sub_schema = p_schema.add_subparsers(dest="schema_cmd", required=True)

    # schema create
    p_schema_create = sub_schema.add_parser("create", help="Create a new schema set")
    p_schema_create.add_argument("key", help="Unique schema set key (e.g., 'customer')")
    p_schema_create.add_argument("name", help="Human-readable name")
    p_schema_create.add_argument(
        "--description", default=None, help="Optional description"
    )
    p_schema_create.set_defaults(func=cmd_schema_create)

    # schema list
    p_schema_list = sub_schema.add_parser("list", help="List all schema sets")
    p_schema_list.set_defaults(func=cmd_schema_list)

    # schema add-version
    p_schema_add = sub_schema.add_parser(
        "add-version", help="Add a version to a schema set"
    )
    p_schema_add.add_argument("key", help="Schema set key")
    p_schema_add.add_argument(
        "--version", type=int, required=True, help="Version number"
    )
    p_schema_add.add_argument("--file", required=True, help="Path to JSON Schema file")
    p_schema_add.add_argument(
        "--parent", type=int, default=None, help="Parent version number"
    )
    p_schema_add.set_defaults(func=cmd_schema_add_version)

    # schema diff
    p_schema_diff = sub_schema.add_parser("diff", help="Show diff between versions")
    p_schema_diff.add_argument("key", help="Schema set key")
    p_schema_diff.add_argument(
        "--from", dest="from_version", type=int, required=True, help="Source version"
    )
    p_schema_diff.add_argument(
        "--to", dest="to_version", type=int, required=True, help="Target version"
    )
    p_schema_diff.set_defaults(func=cmd_schema_diff)

    # Data commands
    p_data = sub.add_parser("data", help="Data ingestion commands")
    sub_data = p_data.add_subparsers(dest="data_cmd", required=True)

    # data ingest
    p_data_ingest = sub_data.add_parser(
        "ingest", help="Ingest JSON data into a version"
    )
    p_data_ingest.add_argument("key", help="Schema set key")
    p_data_ingest.add_argument(
        "--version", type=int, required=True, help="Target version"
    )
    p_data_ingest.add_argument(
        "--file", default=None, help="JSON file to ingest (or stdin)"
    )
    p_data_ingest.set_defaults(func=cmd_data_ingest)

    # Mapping commands
    p_mapping = sub.add_parser("mapping", help="Mapping rule commands")
    sub_mapping = p_mapping.add_subparsers(dest="mapping_cmd", required=True)

    # mapping apply
    p_mapping_apply = sub_mapping.add_parser(
        "apply", help="Apply mapping rules to a version"
    )
    p_mapping_apply.add_argument("key", help="Schema set key")
    p_mapping_apply.add_argument(
        "--version", type=int, required=True, help="Target version"
    )
    p_mapping_apply.add_argument(
        "--file", required=True, help="Path to mapping rules JSON file"
    )
    p_mapping_apply.set_defaults(func=cmd_mapping_apply)

    # View commands
    p_view = sub.add_parser("view", help="Consolidation view commands")
    sub_view = p_view.add_subparsers(dest="view_cmd", required=True)

    # view build
    p_view_build = sub_view.add_parser("build", help="Build a consolidation view")
    p_view_build.add_argument("key", help="Schema set key")
    p_view_build.add_argument("--name", required=True, help="View name")
    p_view_build.add_argument(
        "--include", required=True, help="Comma-separated version numbers"
    )
    p_view_build.add_argument(
        "--target", default="latest", help="Target version or 'latest'"
    )
    p_view_build.add_argument(
        "--mode", choices=["selectable", "db_view"], default="selectable"
    )
    p_view_build.set_defaults(func=cmd_view_build)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
