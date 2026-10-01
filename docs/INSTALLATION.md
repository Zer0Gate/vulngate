# Installation transactions and version identity

The installer requires Python >=3.10 and a POSIX filesystem with flock,
symlinks, rename and directory fsync (macOS/Linux). Native Windows is not a
supported installer backend. Run from a trusted source checkout:

```sh
./install.sh
# Source deployment only, without changing Codex activation:
./install.sh --no-enable
```

`PLUGIN_HOME` selects the parent of the `vulngate` current-version link.
`VULNGATE_MARKETPLACE` selects a marketplace JSON file. The default personal
marketplace is discovered by Codex implicitly; a non-default marketplace must
already be configured in Codex before enabling. `CODEX_BIN` can select the CLI.
Invalid names, malformed JSON and conflicting non-plugin paths are rejected.

## What is deployed

Only the explicit payload roots in `scripts/install_plugin.py` are copied. Audit
state, reports, ledgers, credentials and repository metadata outside those roots
are not shipped. Review source payload roots before installation: an allowlist of
directories is not a secret scanner for arbitrary files placed inside them.

The content manifest records each regular file's relative path, SHA-256 and
executable bit. Symlink payloads are rejected. Bytecode and OS metadata do not
participate in source identity. This is integrity checking, not publisher
authentication: verify release provenance separately. The installer's owner can
still chmod or replace files; the target's OS sandbox must deny access to the
trusted tool tree.

The source manifest version is preserved. Reinstalling identical content is
idempotent. Different content with the same version is rejected once that version
has a retained generation. For development, use Codex's plugin-creator
`update_plugin_cachebuster.py` helper on the source before installing; release
builds must carry their own unique version identity. Do not edit a retained tree.

## Publication and recovery

State is stored in `<PLUGIN_HOME>/.vulngate-install/` (0700):

- `generations/<tree-sha256>/`: read-only retained source trees;
- `install.lock`: advisory installer lock, plus a marketplace-level lock;
- `transaction.json`: durable before/after state for current link, marketplace
  source link and marketplace JSON;
- `receipt.json`: last committed version, tree identity and activation result;
- `last-recovery.json`: filesystem restoration and external activation status;
- `legacy-<id>/`: retained pre-transaction installation directories.

Files are staged and checked before publication. Each link/file replacement is
atomic; the multi-path installation as a whole is recoverable, not a single
atomic filesystem operation. The first migration from a real directory has a
short gap between moving that directory and creating the current link. Stop
audits before upgrading. Cooperating installers serialize with locks; unexpected
external edits make recovery stop rather than overwrite operator changes.

Ordinary failures restore previous pointers/configuration. Abrupt process death
leaves a journal; rerun the same command with the same destinations to recover
before attempting installation. A journal already marked committed completes its
receipt instead of reverting the installation. Do not delete the journal or
legacy backup to bypass an error. Persistent disk failure can prevent rollback;
restore storage availability and retry. File/directory fsync reduces crash risk
but does not assert that every storage device provides power-loss durability.

## Codex activation is a separate boundary

Activation runs `codex plugin add vulngate@<validated-marketplace-name>` and checks
the CLI-reported installed root against the generation's content identity. CLI
failure, absent installation receipt, or mismatched content is not success.
Filesystem rollback does **not** prove Codex's external cache/configuration was
rolled back; the recovery record says activation is unknown once it started.
Recheck activation and retry after resolving the cause. `--no-enable` does not
claim the running plugin was updated.

Old source generations are retained without automatic GC. This preserves data
for recovery, but is not a Codex cache lease. No old-version cache path is ever
redirected to new code. An old thread whose pinned cache disappears must stop;
start a new Codex thread after verified activation.

## Validation

`tests/test_install_transaction.py` injects abrupt process exits after generation
rename, legacy migration, each publication boundary and the durable commit. It
also checks concurrent installation, lock timeout, same-version conflicts,
tampering, symlinks, disk-write failure, external edits and wrong activation
identity. `tests/test_codex_integration.py` verifies actual packaged CLI execution,
deleted-file upgrades, legacy rollback and absence of mutable cache aliases.
These tests do not replace clean-environment installation of a published release.
