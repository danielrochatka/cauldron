# Flat-file workspace

`cauldron-workspace-flatfile` gives the flat-file provider a scratch space for
change-sets, snapshots, previews, and file locks. It is optional — the CMS
still works without it — but is required whenever an editor UI needs to stage
changes before publishing.

## Layout

```
workspace_root/
  change-sets/<id>/manifest.json + payload.json (+ result.json)
  snapshots/<id>/<file>.md + snapshot.json
  previews/<id>/...
  temporary/
  locks/*.lock
```

## `WorkspaceConfig`

```python
from cauldron_workspace_flatfile import WorkspaceConfig

cfg = WorkspaceConfig(workspace_root="/var/lib/mysite/.cauldron/workspace")
```

The config resolves and exposes the well-known subdirectories as properties.

## `ChangeSetStore`

Persist `ContentChangeSet` objects with `create()`, inspect the current state
with `get_state()`, and drive the state machine with `transition()`:

```
proposed  ->  validated  ->  applied
      \--> rejected / failed
```

Terminal states (`applied`, `rejected`, `failed`) refuse further transitions.

## `SnapshotService`

Before applying a change-set the workspace captures the canonical files that
will be touched. `rollback()` restores them; if a canonical file has changed
since the snapshot was taken and `force=False`, `SnapshotConflict` is raised so
the caller can reconcile before overwriting.

## `WorkspaceLock`

Wraps `filelock.FileLock` so multiple processes cannot mutate the same file
tree at the same time.

## Path safety

`safe_resolve()` normalizes every path against the workspace root and refuses
absolute paths, `..` traversal, and symlinks that would escape.

## System checks

- `cauldron.workspace.flatfile.I500` — configuration looks healthy (info).
- `cauldron.workspace.flatfile.E500` — module config is not a dict.
- `cauldron.workspace.flatfile.E501` — `workspace_root` is not absolute.
