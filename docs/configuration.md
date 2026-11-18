# Configuration

The settings live in [`src/sealed_panel/defaults.toml`](../src/sealed_panel/defaults.toml).
There are two ways to change one.

- **A spec** may set the keys of the `[run]` table at its top level, for that run only:
  `judges_per_persona`, `max_rounds`, `control_max`, `good_min`, `noise_floor_min` and
  `framings`. `init` writes the effective value of every one of them into the frozen spec, so
  the rubric hash covers them, the configured reading stances included.
- **A configuration file** passed to `sealed-panel init --config PATH` may set any key, in the
  same TOML layout as `defaults.toml`. Only the keys you set change; the rest keep their
  default. No other command accepts `--config`.

The defaults define the shape. An override that names an unknown key, or gives a value of
another type, is refused. A key 0.4.0 or 0.4.1 removed is refused with a message that names
it.

Settings that guard the protocol have hard bounds, listed below. A value outside its bounds is
refused with a message naming the key, whether it comes from a configuration file or from a
spec. Within those bounds a configuration can loosen or tighten a value, and every value
that differs from the defaults is printed in the report header and by `status`. What it cannot
do: raise a severity cap above its ceiling, lower the quote floors or raise the bad-quote share
past their bounds. The normalisation that texts go through before they are compared, and the
injection patterns, are fixed in code.

At `init`, the effective configuration is written to `config.json` in the run directory and its
sha256 to `config.lock`. Every later command reads that frozen copy and refuses if it was
edited, or if it lacks a key the defaults hold or holds one they do not know (a key with a dot
included). The report header and the output of `status` list every setting that differs
from the packaged defaults, including the per-run keys of the spec, so a loosened or tightened
setting is always visible next to the grade.

Example override file:

```toml
[quotes]
min_chars = 16

[severity.caps]
material = 3

[transcript]
format = "rollout-jsonl"
```

## `[run]`: per-run defaults

| Key | Default | Bounds | Meaning |
|---|---|---|---|
| `judges_per_persona` | `3` | at least `limits.min_judges_per_persona`, at most 9 | Judges briefed per persona and round. |
| `max_rounds` | `6` | at least `limits.min_max_rounds` | Loop rounds before CAP. |
| `control_max` | `2.0` | 1 to 2.5 | A judge whose mean score on the control is above this is discarded. |
| `good_min` | `4.0` | 3.5 to 5 | A judge whose mean score on the known-good is below this is discarded. |
| `noise_floor_min` | `0.5` | 0.25 to 1 | Lowest value the measured noise can take. |
| `framings` | three stances | at least one non-empty stance | Reading stances rotated across a persona's judges, with a random offset per round. |

## `[limits]`

| Key | Default | Bounds | Meaning |
|---|---|---|---|
| `min_judges_per_persona` | `2` | at least `min_valid_judges` | Lowest `judges_per_persona` a spec may ask for. |
| `min_max_rounds` | `1` | at least 1 | Lowest `max_rounds` a spec may ask for. |
| `min_valid_judges` | `2` | at least 2 | Valid judges needed per persona, and verified scores per dimension, to aggregate a round. |
| `max_replacements` | `2` | 0 to 5 | Rejected attempts allowed per judge slot and round before the next real attempt is refused. Void attempts do not count: they have their own cap of 3 per slot and round, in code. |

## `[severity]`

| Key | Default | Bounds | Meaning |
|---|---|---|---|
| `caps` | `blocking = 3, material = 4, cosmetic = 5, none = 5` | exactly these four names, in this order; values 1 to 5, not decreasing; blocking at most 3, material at most 4 | The highest score each severity allows. |
| `findings` | `["blocking", "material"]` | must include `blocking`; only names from `caps` | Severities passed to the author as findings. |

## `[quotes]`

| Key | Default | Bounds | Meaning |
|---|---|---|---|
| `min_chars` | `12` | at least 12 (the default is the floor) | A quote shorter than this, once normalised, locates nothing and voids the score. |
| `min_fragment_chars` | `6` | 6 to `min_chars` (the default is the floor) | When an ellipsis joins fragments, each fragment must be at least this long. |
| `max_bad_share` | `"1/3"` | 0 to 1/3 | A judge whose share of unverified quotes is above this fraction is discarded. Written as a fraction string so the comparison is exact. |

## Normalisation (fixed since 0.4.0)

Before 0.4.0, a `[text]` table set the characters stripped and the punctuation map that quote
matching used. 0.4.0 fixed both in code, at their 0.3.1 defaults (`text.STRIP_CHARS` and
`text.PUNCTUATION`), and every comparison of texts uses the same normalisation: Unicode NFKC,
the punctuation map (curly quotes and guillemets to straight quotes, en and em dashes to a
hyphen), the markdown characters `` *_`#> `` removed, whitespace collapsed, case folded. That
covers quote matching, the injection scan, the re-roll hash, the identity of the control and
the candidate, whether the previous version or the baseline rides along, the one-quote flag,
and the whole-brief check of a transcript.

A configuration file that still sets a `[text]` key is refused with a message naming the key.
A run created before 0.4.0 (one without `run.json`) is refused by every command, so none is ever
graded under the constants.

## Injection patterns (fixed since 0.4.0)

Before 0.4.0, `[injection] patterns` could be extended (every default pattern had to stay) and
`[injection] samples` gave each a line it must match. 0.4.0 fixed the five 0.3.1 defaults in
code (`text.INJECTION_PATTERNS`), one of them French, for texts written in French. They run on
normalised text (see Normalisation). Every text placed in a brief is scanned (candidate,
control, previous version, baseline, known-good); a match is flagged per item in the author's
findings and in the report, and blocks CONVERGED and the sealed grade unless the spec has
`injection_acknowledged`. A match in the spec's title, purpose, persona descriptions or
declared choices refuses `init`. A configuration file that still sets an `[injection]` key is
refused with a message naming the key.

## `[transcript]`

A judge's transcript is read by a host adapter, one per transcript format; the core reads only
what the adapter returns: the prompt, the reply, the tools called, the agent id, the inputs that
reached the judge after its first output, and whether the session carries another session's
history. [integrations.md](integrations.md#transcript-formats) says which host writes which
format, where its transcripts are, and what its adapter refuses.

| Key | Default | Bounds | Meaning |
|---|---|---|---|
| `format` | `"agent-jsonl"` | `agent-jsonl` or `rollout-jsonl` | The format of the judges' transcripts: the adapter that reads them. Frozen at `init`, like every setting. |

The list of formats is closed: a format is added in code, with its tests, never by a setting.
An adapter reads its format by its own structure and refuses a file it does not recognise,
rather than guess; it never skips an entry of a type it checks. If no reply is found, `record`
refuses.

Until 0.4.1, `[transcript]` held 18 field names (`role_key`, `handback_tool` and the others) for
the one reader there was. A configuration file that still sets one is refused with a message
naming each. A run frozen by 0.4.0 holds them and no `format`: after its `config.lock` is
checked against the file as written, it reads as `format = "agent-jsonl"` when they hold the
0.4.0 defaults, which that adapter reads the same way; a run frozen with other field names is
refused, naming them, and is finished with 0.4.0 (see
[protocol.md](protocol.md#11-the-run-format)).

`record --void` fails closed: any non-empty assistant text and any call, whichever its name,
counts as a reply, because a runtime may deliver the answer through a tool no adapter knows of.
A judge whose prompt held the whole brief and that left any of those cannot be voided.

## Why each section is a setting

- `[run]`: the per-run choices a commissioner makes (panel size, round limit, calibration
  limits, stances); they are also spec keys, and frozen into the spec.
- `[limits]`: the floors of those choices; an organisation may want them stricter.
- `[severity]`: how much a flaw costs; bounded so it can only cost more.
- `[quotes]`: how strict the quote check is. The quote floors are the defaults, so quotes can
  only be made stricter. Which typography the check forgives is no longer a setting: a legal
  `[text]` override could only loosen some check in one direction or another, so 0.4.0 fixed
  it in code.
- `[transcript]`: agent hosts write transcripts in different formats; the setting names the
  adapter that reads the run's judges, and is frozen so a run reads every judge the same way.
- `[judge]`: which program judges, how long it may take, and which credentials it may see,
  depend on the machine and the model; they are frozen so a run keeps one judge.

## `[judge]`

The judge runner (`sealed-panel judge`, see [cli.md](cli.md)). Set it in the file given to `init
--config`, or, for the MCP server, in the file given to `sealed-panel mcp --config`, outside its
root: the server reads that file once, freezes it into every run it starts and changes only runs
frozen with it, so the caller of a tool never picks its judge. It is frozen into the run like
every other setting, so the judges cannot change in the middle of a run, and a command that
differs from the default (empty) is listed in full next to the grade. A run with a judge command
takes runner replies only. `model_id` declares the model the judges run on, in host mode as
well, and is printed next to the sealed grade.

```toml
[judge]
command = ["python", "/path/to/my_judge.py"]
timeout_s = 300
env_allow = ["MY_MODEL_API_KEY"]
model_id = "my-model-2026-01-15"
```

| Key | Default | Bounds | Meaning |
|---|---|---|---|
| `command` | `[]` | a list of non-empty strings | The judge command as an argument list, started once per brief without a shell. It reads the brief on stdin and prints the reply on stdout. Empty means host mode: your agent host runs the judges and `record` stores their replies. The list is stored with every reply and shown in the report: never put a credential in it. |
| `timeout_s` | `300` | 1 to 3600 | Seconds a judge process may run. Past it, the process and every process it started are stopped, and the attempt is recorded as void. |
| `env_allow` | `[]` | environment variable names | Variables passed to the judge process besides `PATH`, `HOME` and `LANG` (each only when set). Each attempt stores a sha256 of each value, never the value, and the report flags a round whose attempts ran under different values. For credentials: name the model in `command`. |
| `max_reply_bytes` | `1048576` | 1024 to 16777216 | Bytes a judge may write to stdout. Past it, the process is stopped and the attempt is recorded as void; only the bytes up to the cap are kept. |
| `max_stderr_bytes` | `1048576` | 1024 to 16777216 | The same for stderr, kept in the log beside the brief. |
| `model_id` | `""` | one printable line, at most 200 characters | The model the judges run on, as free text: frozen into the run and printed next to the sealed grade ("not declared" when empty). Pin a dated model id, never an alias: an alias may point to another model the next day, and two grades under one alias may come from two models. sealed-panel does not check that the command uses it. |

The judge process never inherits the environment of the process that runs sealed-panel. That
environment often holds credentials (cloud tokens, repository tokens, other API keys) that a
judge has no use for, and a judge is a program that reads text written by someone else. So it
gets `PATH`, `HOME` and `LANG`, the names you list in `env_allow`, and nothing else: a key
reaches a judge only when you named it. The names passed are stored with each reply, with a
sha256 of each allowed value salted with the run's own random salt (`env.salt`, written by
`init`); the values never are. A round whose attempts ran under different values of an allowed
variable is flagged in the report, and so is a sealed panel that ran under other values than the
loop rounds. The salt stops a lookup in a table made in advance; whoever holds the run directory
holds the salt too and can guess a short value, so keep credentials long. Put the model in
`command` (the template takes it after `--`), where the run freezes it and the report shows it,
and keep the environment for credentials; declare the same model in `model_id`.

## `[mcp]`

| Key | Default | Bounds | Meaning |
|---|---|---|---|
| `root` | `""` | absolute or starting with `~` | The MCP server's root, read from the file given to `sealed-panel mcp --config`: every path a tool takes must resolve under it, symbolic links followed. Empty means the directory the server starts in; `--root` overrides it. It places files and changes no grade, so it is not listed among the settings that differ from the defaults. |

## The library and learning (no longer settings since 0.4.0)

Before 0.4.0, `[library]` held `path` (where the rubric library lives), `learn` (whether every
sealed `aggregate` wrote learned candidates) and the two thresholds of `control_too_close`. Now
the library is `--library DIR`, else `$SEALED_PANEL_RUBRICS`, else the user-level default (see
[rubrics.md](rubrics.md)); learning runs only through `sealed-panel learning learn RUN`; and
the thresholds are constants of the learning module (4.0 and 1.0, their 0.3.1 defaults). A file
that still sets a `[library]` key is refused with a message naming it.

## What stays in code

Some values are part of the instrument or of the protocol, not settings, and stay in the code
as named constants:

- the brief texts (header, rubric heading, judging steps, output instructions), the item tag,
  the label pool, and the default 2 and 4 anchors (`brief.py`): they are the instrument, and
  every run with the same version of sealed-panel asks the judges the same thing;
- the size of the brief id (6 random bytes) and of the shuffle seed (64 bits) (`brief.py`);
- the cap of 3 void attempts per judge slot and round (`seat.py`);
- the judge runner's base environment (`PATH`, `HOME`, `LANG`), the size of its agent ids (6
  random bytes), the grace period after it stops a process (5 seconds), and the size of a read
  (64 KiB) (`runner.py`);
- every file sealed-panel creates in a run (records, briefs, lock files, the judge's stderr log)
  written owner-only (0600), and every directory created 0700, whatever the umask or the mode of
  the file it replaces (`store.py`, `runner.py`);
- the length above which the report shows a value by hash (80 characters) (`result.py`);
- the score scale, integers 1 to 5, with 5 as the convergence target, and the required anchors
  1, 3 and 5 (`decide.py`, `rubric.py`);
- the stop rule's window of two rounds and the suspect-jump threshold of 2 (`decide.py`);
- the decision names, the run directory layout and its file names;
- widths and scan sizes: the text hash (16 hex characters), file-name parts of recorded
  replies, the rubric hash in the report (12), the characters searched for an agent id, and
  the largest file read when looking for unrecorded agents;
- the file extensions that make a `facts` text look like a path (`run.py`);
- the learning thresholds (two loop judges for a new dimension, two judges of one round to
  sharpen an anchor) and the suggest weights (`learning/learn.py`, `templates.py`);
- rounding of stored statistics, and the error, findings and report wording.
