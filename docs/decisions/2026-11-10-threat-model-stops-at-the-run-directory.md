# The threat model stops at the run directory

- Decided: 0.4.0 (2026-11-10). Recorded: 2026-11-10.
- Status: in force.

## Context

Up to 0.3.1, the filesystem code defended run files against a writer inside the run directory:
writes through directory descriptors with `O_NOFOLLOW`, reads that refused links and FIFOs, a
per-seat marker file with pid and age liveness checks, a rename-to-stale step with a re-read and
a link restore, and a check for dotted keys in a frozen configuration. `SECURITY.md` listed
planted links, planted FIFOs and planted markers as in scope, while it also said that
hand-written run files are out of scope: the two statements disagreed. The marker code was the
largest single block of the runner, and host-mode `record`, which has the same race as `judge`,
had no guard at all.

## Decision

The adversary is the author driving the documented commands and MCP tools, a judge whose output
is untrusted, and texts that may carry injections. Someone with shell access to the run
directory or the transcript folder is out of scope. Within that model:

- every run file is written to a private temporary file in its directory, then moved over the
  target (`os.replace`), so a reader never sees half a file and a link on the file is replaced;
- one guard, `store.contained()` (resolve, then require the path to stay under a root), confines
  every path an MCP tool takes, a spec's `facts_file`, and a transcript to its transcript roots;
- one `fcntl.flock` per judge seat, held from the seat check to the stored attempt by `judge`,
  host-mode `record` and `aggregate`, replaces the marker; a started record left by a runner
  that died becomes a void for the next call.

## Consequences

- The FIFO checks, the link-refusing reads, the descriptor-based writes and their fallback, the
  marker liveness heuristics and the dotted-key check are removed. `SECURITY.md` carries a guard
  map: each guard, the threat it answered, its scope, and what pins what remains.
- Host-mode `record` becomes safe against concurrent calls on one seat. The run-wide checks (an
  agent id or a reply used once per run, a round briefed once) run under a run lock taken after
  any seat lock, so they hold under concurrent calls too.
- A run is never created inside another run, so the author cannot put a directory where a
  round's lock or result goes.
- A filesystem without `flock` is refused; `flock` is advisory and may not hold on NFS.
- Every file sealed-panel creates in a run is now owner-only (0600), the lock files and the
  judge's stderr log included, and every directory 0700; a file's earlier mode is not kept.
- A writer inside the run directory can still plant a link on a folder or delete a started
  record. That was never closable by code that cannot tell who wrote a file.
