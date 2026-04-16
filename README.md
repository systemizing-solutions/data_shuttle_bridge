# data_shuttle_bridge

A comprehensive data synchronization and backup solution:
- **DATA:** What you are moving
- **S.H.U.T.T.L.E** - **S**ync **H**ub **U**tility, **T**ransfers & **T**wo-way **L**inked **E**ndpoints
- **B.R.I.D.G.E** - **B**atch **R**eplication & **I**ncremental **D**elta **G**ateway **E**ngine

## Core Features

### Data Synchronization (SQL)
Local-first, bidirectional sync engine for **SQLAlchemy** and **SQLModel** with:

- K-sorted **64-bit integer IDs** (`KSortedID`) to avoid PK collisions across devices
- Per-row **versioning**, timestamps, and deletion markers
- Automatic **change capture** via ORM events
- **Schema auto-discovery** (no manual field lists) and **topological** parent→child apply order
- Pluggable **HTTP transport** + ready-to-use **Flask** sync blueprint
- **Client node leasing** (unique `node_id` per device) with a simple server endpoint
- **Write watermarking** with node tracking to prevent circular syncs
- Tiny **CLI** (`localfirst-sync`) to register/ensure a device `node_id`

### Schema Versioning & Consolidation
Manage evolving schemas with automatic or explicit mapping rules:

- **JSON Schema contract** - Define schemas using JSON Schema (2020-12 draft)
- **Automatic versioning** - Each schema version gets a physical table (e.g., `customer__v1`, `customer__v2`)
- **Drift detection** - Automatically compute and classify schema changes between versions
- **Flexible mapping** - Support renames, type changes, expressions, and default behaviors
- **Unified queries** - Build UNION ALL views across versions with automatic column alignment
- **Auditable evolution** - All diffs, rules, and view definitions are stored in registry
- **Policy engines** - Configurable default handling for missing/removed fields
- See [SCHEMA_VERSIONING_README.md](./SCHEMA_VERSIONING_README.md) for detailed documentation and examples

### File Backup
Lightweight backup tool with multi-backend support:

- **Multi-backend storage** - Local, S3, GCS, Azure, HTTP, and 25+ more via `fsspec`:
    - Implementations built into fsspec: [Built-in Implementations](https://filesystem-spec.readthedocs.io/en/latest/api.html#implementations)
    - Implementations in separate packages: [Other Known Implementations](https://filesystem-spec.readthedocs.io/en/latest/api.html#external-implementations)
- **Content deduplication** - SHA256-based content addressing automatically deduplicates identical files
- **Snapshot-based recovery** - Point-in-time recovery with full file metadata preservation
- **Automatic deduplication** - Identical content shared across snapshots
- **File metadata** - Preserves permissions, timestamps, and directory structure
- **Extensible architecture** - ChunkingStrategy interface for compression/encryption
- **Comprehensive CLI** - Simple commands for init, backup, snapshots, restore

current_version = "v0.0.1"

## Peer-to-Peer Sync (WireGuard)

Sync data directly between devices over an encrypted WireGuard tunnel — **no central
server, no third-party relay, no data caps or time limits**.

### How it works

```
┌──────────┐   WireGuard tunnel (encrypted, direct)   ┌──────────┐
│  Peer A  │◄────────────────────────────────────────►│  Peer B  │
│ 10.0.0.1 │   UDP hole-punch / UPnP / port-forward   │ 10.0.0.2 │
└──────────┘                                           └──────────┘
```

1. Both peers exchange tokens out-of-band (copy-paste via chat/email)
2. WireGuard creates a direct encrypted tunnel between them
3. The existing HTTP sync protocol runs over the tunnel — zero code changes needed

### Quick start
#### Install WireGuard
[https://www.wireguard.com/install/](https://www.wireguard.com/install/)
```bash
brew install wireguard-tools   # macOS
# apt install wireguard        # Linux
```

#### 
```bash
# Peer A: generate invite
data-shuttle p2p init
data-shuttle p2p invite --sync-port 5000
# → prints an invite token

# Peer B: join (paste the token)
data-shuttle p2p join <invite_token>
# → prints a response token

# Peer A: complete (paste the response)
data-shuttle p2p complete <response_token>

# Both peers: bring tunnel up
sudo data-shuttle p2p up

# Both peers: verify connectivity
data-shuttle p2p status
```

### NAT traversal

Endpoint discovery is automatic — you don't need to know your public IP:

| NAT Type | Works automatically? | Method |
|---|---|---|
| Public IP / no NAT | Yes | Direct connection |
| Full / restricted / port-restricted cone | Yes | STUN + UDP hole-punch |
| UPnP-enabled router | Yes | Auto port-forward |
| Symmetric NAT | No — manual port-forward needed | CLI warns you |

Install `miniupnpc` for automatic UPnP port forwarding: `pip install miniupnpc`

### Programmatic usage

```python
from data_shuttle_bridge.p2p.transport_wireguard import WireGuardPeerTransport

# Peer B connects to Peer A's WireGuard IP
transport = WireGuardPeerTransport(peer_virtual_ip="10.0.0.1", sync_port=5000)
pulled, pushed = sync_engine.pull_then_push(transport, batch=100)
```

See [examples/p2p_sync_demo.py](examples/p2p_sync_demo.py) for a full working example.

## Project Structure

```
src/data_shuttle_bridge/
├── sql/                          # Database synchronization and versioning
│   ├── models.py                 # ORM models and mixins
│   ├── sync.py                   # Sync engine
│   ├── transport.py              # HTTP transport
│   ├── blueprint.py              # Flask sync blueprint
│   ├── versioning_models.py      # Schema versioning ORM models
│   ├── schema_registry.py        # Schema registry runtime
│   ├── jsonschema_types.py       # JSON Schema to SQLAlchemy mapping
│   ├── diffing.py                # Schema diff engine
│   ├── policy.py                 # Drift policy engines
│   ├── mapping.py                # Mapping rules
│   ├── view_builder.py           # Consolidation view builder
│   ├── dialects/                 # Database dialect adapters
│   └── ...
├── file_backup/                  # File backup
│   ├── cli.py                    # CLI commands
│   ├── runtime.py                # Core operations
│   ├── repo/repository.py        # Repository management
│   ├── pipeline/chunking.py      # Chunking strategies
│   └── ...
├── p2p/                          # Peer-to-peer WireGuard sync
│   ├── cli.py                    # P2P CLI commands
│   ├── wireguard.py              # Key generation, config, tunnel lifecycle
│   ├── nat.py                    # STUN, UPnP/NAT-PMP, NAT traversal
│   ├── invite.py                 # Invite/join token exchange
│   ├── transport_wireguard.py    # WireGuardPeerTransport
│   └── __init__.py
├── cli.py                        # Main CLI entry point
└── __init__.py                   # Package exports

tests/
├── test_sql_sync.py              # SQL sync tests
├── test_file_backup.py           # File backup tests
└── __init__.py

examples/
├── sqlalchemy/                   # SQLAlchemy examples
├── sqlmodel/                     # SQLModel examples
└── file_backup/                  # File backup examples
```


## SQL Sync Usage
### How Sync State Tracking Works

Each side of a sync connection (client and server) maintains its own `SyncState` record to independently track synchronization progress:

**Client-side tracking (in local database):**
- `peer_id`: The server's identifier (e.g., `"server"`)
- `last_pulled_change_id`: Highest change ID the client has pulled from the server
- `last_pushed_change_id`: Highest change ID the client has pushed to the server

**Server-side tracking (in remote database):**
- `peer_id`: The client's identifier (e.g., client `node_id` like `"1"`)
- `last_pulled_change_id`: Highest change ID the server has pulled from the client
- `last_pushed_change_id`: Highest change ID the server has pushed to the client

### Sync Sequence

When `pull_then_push()` is called:

1. **Pull Phase**: Client queries server for all changes since `last_pulled_change_id`, excluding changes with its own `node_id` (write watermarking)
2. **Apply**: Client applies received changes and updates its `last_pulled_change_id`
3. **Push Phase**: Client queries its local changelog for all changes since `last_pushed_change_id`, excluding changes from other nodes
4. **Send**: Client sends these changes to server and updates its `last_pushed_change_id`

This bidirectional tracking ensures each side independently knows what it has sent and received, preventing data loss and enabling proper conflict resolution via **Last-Write-Wins (LWW)** or **Version Strict** policies.

### Dirty Field Tracking

To prevent meaningless changelog entries and version increments, data_shuttle_bridge implements **dirty field tracking** at the ORM event level:

**Problem it solves:**
When rows are synced from the remote peer, the database's `onupdate: func.now()` auto-updates the `updated_at` timestamp. Previously, this triggered SQLAlchemy's `before_update` hook, which would increment the version and create a changelog entry, even though no actual data changed. This caused unnecessary version bumps (e.g., version jumping to 7 without any real modifications).

**How it works:**
- The `before_update` hook checks if any actual data columns changed (excluding system fields: `updated_at`, `version`, `deleted_at`, `id`)
- Uses SQLAlchemy's `attributes.get_history()` to inspect which fields were truly modified
- Only increments the version if real data changed
- The `after_update` hook only creates changelog entries for meaningful changes

**Result:**
- Auto-timestamp updates don't pollute the changelog
- Versions only increment on actual data modifications
- Clean, accurate change history with only meaningful entries

## Quickstart

### Install
```shell
pip install data_shuttle_bridge
```

## Usage Guide

### Understanding Nodes

In data_shuttle_bridge, a **node** represents a unique device or client instance in your sync network. Each node has:

- **`node_id`**: A string identifier (e.g., `"1"`, `"device-123"`, `"server-node"`) that uniquely identifies this instance
- **`KSortedID` generator**: Uses the `node_id` to generate globally unique 64-bit IDs that never collide across devices
- **Watermarking**: Tracks which changes originated from this node to prevent circular syncs

#### Node Setup

**Option 1: Using ClientNodeManager (Recommended for Clients)**

The `ClientNodeManager` handles node registration automatically. Simply call `set_id_generator()` once during startup:

```python
from data_shuttle_bridge import ClientNodeManager, set_id_generator

# 1. Register/ensure this client has a unique node_id from the server
manager = ClientNodeManager()
node_id = manager.ensure_node_id("http://your-server.com")
print(f"This client's node_id: {node_id}")

# 2. Set up the ID generator (call this ONCE at startup)
set_id_generator(node_id)

# Now all your models automatically get unique IDs!
```

**Option 2: Manual Node Assignment (For Testing or Server)**

```python
from data_shuttle_bridge import set_id_generator

# Set the node_id for this instance
set_id_generator("server-node")

# All models now automatically generate IDs
```

**Option 3: Multi-Tenant Setup (Per-Request)**

For multi-tenant applications, call `set_id_generator()` at the beginning of each request with the tenant's node_id:

```python
from flask import Flask, g
from data_shuttle_bridge import set_id_generator, clear_id_generator

app = Flask(__name__)

@app.before_request
def setup_tenant():
    # Extract tenant node_id from request context
    tenant_id = request.headers.get("X-Tenant-ID")
    set_id_generator(tenant_id)  # Set for this request

@app.teardown_request
def cleanup_tenant(exception):
    clear_id_generator()  # Clean up after request
```

The thread-local storage ensures each request/tenant gets its own ID generator without conflicts.

**Option 4: Manual ID Generation (Advanced)**

For applications that need more control over ID generation, you can use `KSortedID` directly:

```python
from data_shuttle_bridge import KSortedID

# Create your own ID generator with explicit node_id
id_gen = KSortedID(node_id=1)  # node_id must be 0-1023

# Generate IDs manually
next_id = id_gen()

# Use in models
customer = Customer(
    id=id_gen(),  # Manually assign ID
    name="John Doe",
    email="john@example.com"
)
sess.add(customer)
sess.commit()
```

Note: This approach bypasses the automatic `set_id_generator()` setup, so you won't get the watermarking benefits. Use this only if you have specific requirements for ID generation.

### Setting Up a Server

The server acts as the central sync hub, storing all changes and distributing them to clients:

```python
from flask import Flask
from data_shuttle_bridge import (
    sync_blueprint,
    node_registry_blueprint,
    SyncEngine,
    build_schema,
    attach_change_hooks_for_models,
    set_id_generator,
)
from sqlmodel import Session, create_engine

app = Flask(__name__)

# 1. Set up ID generator (server uses a fixed node_id)
set_id_generator("server-node")

# 2. Initialize database
engine = create_engine("sqlite:///server.db")
SessionLocal = sessionmaker(engine, class_=Session)

# 3. Create tables
from your_models import Customer, Order
Base.metadata.create_all(engine)

# 4. Define your models
models = [Customer, Order]

# 5. Attach change tracking hooks
attach_change_hooks_for_models(models)

# 6. Build schema
SCHEMA = build_schema(models)

# 7. Create SyncEngine factory - server uses node_id="server-node"
def engine_factory():
    sess = SessionLocal()
    return SyncEngine(
        session=sess,
        peer_id="client-peer",  # Generic peer identifier
        schema=SCHEMA,
        policy="last_write_wins",  # Or "version_strict"
        node_id="server-node",  # Server's unique node identifier
    )

# 8. Register sync endpoints
app.register_blueprint(sync_blueprint(engine_factory))
app.register_blueprint(node_registry_blueprint(SessionLocal))

if __name__ == "__main__":
    app.run(port=5001)
```

### Setting Up a Client

Clients pull changes from the server, create local data, and push their changes back:

```python
from data_shuttle_bridge import (
    SyncEngine,
    ClientNodeManager,
    build_schema,
    attach_change_hooks_for_models,
    HttpPeerTransport,
    set_id_generator,
)
from sqlmodel import Session, create_engine, select

# 1. Get unique node_id from server
manager = ClientNodeManager()
node_id = manager.ensure_node_id("http://127.0.0.1:5001")

# 2. Set up global ID generator (CRITICAL - enables watermarking)
set_id_generator(node_id)

# 3. Initialize local database
engine = create_engine("sqlite:///local.db")
SessionLocal = sessionmaker(engine, class_=Session)

# 4. Create tables
from your_models import Customer, Order
Base.metadata.create_all(engine)

# 5. Attach change tracking hooks
models = [Customer, Order]
attach_change_hooks_for_models(models)

# 6. Build schema
SCHEMA = build_schema(models)

# 7. Create SyncEngine with node_id
def main():
    with SessionLocal() as sess:
        engine = SyncEngine(
            session=sess,
            peer_id="remote-server",
            schema=SCHEMA,
            policy="last_write_wins",
            node_id=str(node_id),  # THIS IS CRITICAL - enables watermarking
        )
        
        # 8. Create transport to server
        transport = HttpPeerTransport("http://127.0.0.1:5001")
        
        # 9. Sync: Pull changes from server, then push local changes
        pulled, pushed = engine.pull_then_push(transport)
        print(f"Pulled: {pulled}, Pushed: {pushed}")
        
        # 10. Create local data - IDs are auto-generated!
        customer = Customer(
            name="John Doe",
            email="john@example.com"
            # id is automatically generated by set_id_generator()
        )
        sess.add(customer)
        sess.commit()
        
        # 11. Sync again to push the new data to server
        pulled, pushed = engine.pull_then_push(transport)
        print(f"Pulled: {pulled}, Pushed: {pushed}")

if __name__ == "__main__":
    main()
```

### How Nodes Prevent Circular Syncs

Each node tracks its own changes with its `node_id`:

1. **Client creates order** → Changelog entry marked with `node_id="1"`
2. **Client pushes to server** → Server receives and stores in its changelog
3. **Server sends changes to client** → Server excludes changes with `node_id="1"` (client won't re-pull its own data)
4. **Client pulls from server** → Client only receives changes from `node_id="server-node"` or other nodes

This **write watermarking** ensures:
- ✅ No circular syncing (data isn't synced back to its originator)
- ✅ Multiple clients can sync without duplicating each other's data
- ✅ Clean changelog with clear attribution of every change

### Models with Sync Support

Your models now automatically get ID generation! Just inherit from the mixin:

**For SQLModel:**
```python
from sqlmodel import SQLModel, Field
from data_shuttle_bridge.mixins import SyncRowSQLModelMixin

class Customer(SyncRowSQLModelMixin, SQLModel, table=True):
    name: str
    email: str
    # Automatically gets: id (auto-generated), updated_at, version, deleted_at
```

**For SQLAlchemy:**
```python
from sqlalchemy import Column, String
from sqlalchemy.orm import declarative_base
from data_shuttle_bridge.mixins import SyncRowSAMixin

Base = declarative_base()

class Customer(Base, SyncRowSAMixin):
    __tablename__ = "customers"
    name = Column(String)
    email = Column(String)
    # Automatically gets: id (auto-generated), updated_at, version, deleted_at
```

**No more manual ID field definition!** The mixin provides:
- **`id`**: Auto-generated using the global ID generator (set via `set_id_generator()`)
- **`updated_at`**: Timestamp (auto-updated on changes, but doesn't create spurious changelog entries due to dirty field tracking)
- **`version`**: Integer counter (incremented only on real data changes)
- **`deleted_at`**: Soft delete timestamp

## File Backup Usage

The file backup feature provides a lightweight, deduplicating backup tool with multi-backend storage support. It uses fsspec for storage abstraction, allowing backups to any supported backend.

### Quick Start

```bash
# Initialize a backup repository
shuttle_bridge backup init file:///data/backups

# Backup files or directories
shuttle_bridge backup backup file:///data/backups /data/important /etc/config

# List all snapshots
shuttle_bridge backup snapshots file:///data/backups

# Restore latest snapshot
shuttle_bridge backup restore file:///data/backups /restore/location

# Restore specific snapshot
shuttle_bridge backup restore file:///data/backups /restore/location --snapshot-id abc123
```

### Using Different Storage Backends

`file_backup` supports any fsspec-compatible backend:

```bash
# Local filesystem
shuttle_bridge backup init file:///backups

# AWS S3
shuttle_bridge backup init s3://my-bucket/backups

# Google Cloud Storage
shuttle_bridge backup init gs://my-bucket/backups

# Azure Blob Storage
shuttle_bridge backup init az://my-container/backups

# HTTP endpoint
shuttle_bridge backup init http://backup-server.com/repo
```

### Python API Usage

```python
from data_shuttle_bridge.file_backup import (
    init_repo,
    run_backup,
    list_snapshots,
    run_restore,
)

# Initialize repository
init_repo("file:///data/backups")

# Create backup
snapshot_id = run_backup("file:///data/backups", ["/data/important"])
print(f"Created snapshot: {snapshot_id}")

# List snapshots
list_snapshots("file:///data/backups")

# Restore from latest snapshot
run_restore("file:///data/backups", "/restore/location")

# Restore from specific snapshot
run_restore("file:///data/backups", "/restore/location", snapshot_id=snapshot_id)
```

### How File Backup Works

**Content-Addressed Storage:**
- Files are split into chunks (default 4MB)
- Each chunk is hashed with SHA256
- Chunks are stored by hash (`objects/<hh>/<hh>/<hash>`)
- Identical content is automatically deduplicated

**Snapshots:**
- Each backup creates a snapshot manifest
- Snapshots contain file list with chunk references
- Snapshots are JSON files with metadata (timestamp, hostname, file entries)
- Point-in-time recovery is instant - just restore from a snapshot

**Repository Structure:**
```
backup-repo/
├── config.json          # Repository metadata
├── objects/             # Content-addressed blobs
│   ├── ab/
│   │   ├── cd/
│   │   │   └── abcdef...  # File chunks
│   │   └── ...
│   └── ...
├── snapshots/           # Backup snapshots
│   ├── 1704200000_abc123.json
│   └── ...
└── indexes/             # File->blob mappings
    ├── abc123.json
    └── ...
```

### Extensibility

`file_backup` is designed for extension via the `ChunkingStrategy` interface. You can implement custom strategies for:

```python
from data_shuttle_bridge.file_backup.pipeline import ChunkingStrategy

class CompressedChunker(ChunkingStrategy):
    """Custom chunking with compression."""
    
    def chunk(self, file_obj):
        """Chunk and compress data."""
        # Your compression logic here
        pass
```

## Tests
```shell
pytest .
pytest --cov=src --cov-report html
```

### Testing File Backup

```bash
# Run file backup tests
pytest tests/test_file_backup.py -v

# Run the example
python example_file_backup.py
```

## Development
### Install the local environment
```shell
python -m venv venv
```

#### Windows
```shell
venv/scripts/activate
```

#### Mac/Linux
```shell
source venv/bin/activate
```

### Install the local `data_shuttle_bridge` project
#### Install `poetry` package manager
```shell
pip install poetry
```

#### Lock `poetry` dependencies
```shell
poetry cache clear pypi --all -n
poetry lock
```

#### Install `data_shuttle_bridge` package via `poetry` (including dependencies)
```shell
poetry install
```

### Test
```shell
pytest
coverage run -m pytest
coverage report
coverage html
mypy --html-report mypy_report .
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics --format=html --htmldir="flake8_report/basic" --exclude=venv
flake8 . --count --exit-zero --max-complexity=11 --max-line-length=127 --statistics --format=html --htmldir="flake8_report/complexity" --exclude=venv
```

### BumpVer
With the CLI command `bumpver`, you can search for and update version strings in your project files. It has a flexible pattern syntax to support many version schemes (SemVer, CalVer or otherwise).
Run BumbVer with:
```shell
bumpver update --major
bumpver update --minor
bumpver update --patch
```

### Build
```shell
poetry build
```

### Publish
```shell
poetry publish
```

### Automated PyPI Publishing

This project uses GitHub Actions to automatically publish to PyPI when a new version tag is pushed.

#### Setup (One-time configuration)

1. **Register a Trusted Publisher on PyPI**:
   - Go to https://pypi.org/manage/account/publishing/
   - Click "Add a new pending publisher"
   - Fill in the following details:
     - **PyPI Project Name**: `data_shuttle_bridge`
     - **Owner**: `RyanJulyan` (your GitHub username)
     - **Repository name**: `data_shuttle_bridge`
     - **Workflow name**: `publish.yml`
     - **Environment name**: `pypi`
   - Click "Add pending publisher"

#### How it works

When you use `bumpver` to update the version:
```shell
bumpver update --patch  # or --minor, --major
```

This will:
1. Update the version in `pyproject.toml`, `src/data_shuttle_bridge/__init__.py`, and `README.md`
2. Create a git commit with the version bump
3. Create a git tag (e.g., `4.0.1`)
4. Push the tag to GitHub

GitHub Actions will automatically detect the new tag and:
1. Build the distribution packages (wheel and source)
2. Publish to PyPI using the trusted publisher authentication

#### Security

This approach uses **OpenID Connect (OIDC) Trusted Publishers**, which is more secure than API tokens because:
- ✅ No credentials are stored in GitHub secrets
- ✅ Only this specific workflow can publish
- ✅ Only from this specific repository
- ✅ PyPI automatically verifies the request is legitimate
