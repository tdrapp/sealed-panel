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
another type, is refused. One table accepts new keys: `text.punctuation`.

Settings that guard the protocol have hard bounds, listed below. A value outside its bounds is
refused with a message naming the key, whether it comes from a configuration file or from a
spec. Within those bounds a configuration can loosen or tighten a value, and every value
that differs from the defaults is printed in the report header and by `status`. What it cannot
do: raise a severity cap above its ceiling, lower the quote floors or raise the bad-quote share
past their bounds, drop a default injection pattern, add a pattern that matches nothing, or
normalise away letters, digits or spaces before quotes are compared.

At `init`, the effective configuration is written to `config.json` in the run directory and its
sha256 to `config.lock`. Every later command reads that frozen copy and refuses if it was
edited. The report header and the output of `status` list every setting that differs from the
packaged defaults, including the per-run keys of the spec, so a loosened or tightened setting
is always visible next to the grade.

Example override file:

```toml
[quotes]
min_chars = 16

[severity.caps]
material = 3

[transcript]
agent_id_key = "session_id"
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

## `[text]`

| Key | Default | Meaning |
|---|---|---|
| `strip_chars` | `` "*_`#>" `` | Characters removed before quotes are compared (markdown emphasis and headings). Held at the default set (a safety floor and ceiling): adding a character would loosen the quote check, removing one would fail quotes of markdown-emphasised text. Quote matching only: every other check uses the default (see below). |
| `punctuation` | curly quotes, guillemets, dashes, ellipsis | Characters mapped to plain ones before quotes are compared, written as TOML escapes. Each key is one character that is not a letter, a digit or whitespace, before or after case folding (non-breaking spaces need no entry: NFKC already turns them into plain spaces). Each value is exactly one character that is not whitespace, not a letter or digit before or after case folding, and not a stripped character; the one exception is the built-in entry for the ellipsis character, which keeps its `...`, and no other key may map to `...`. A key may not be a stripped character either. So a mapping turns one punctuation character into another: it never deletes one, splits a word or joins two. A value of `.` is legal, so any run of mapped and literal dots that normalises to an ellipsis (`---`, `- - -` or `.--` with `"-" = "."`) reads as one, and joins quote fragments; a judge could write `...` anyway, so no reach is gained. NFKC turns the ellipsis character into `...` before the map runs, so the built-in entry for it has no effect; it stays so that runs frozen with it remain valid. Text is also put in Unicode NFKC form, whitespace is collapsed, and case is folded. Quote matching only: every other check uses the default map (see below). |

The run's `[text]` settings decide one thing: whether a judge's quote is found in an item.
Every other check normalises with the built-in settings, whatever the configuration says,
because a legal override would loosen each of them in one direction or the other. Merging two
punctuation characters (`"," = "."`) and undoing a default mapping (a key mapped to itself,
such as the right single quote) would move each check as follows, if it used the run's
settings:

| Check | Code | Normalisation | A merge would make it | An undone mapping would make it |
|---|---|---|---|---|
| A quote is found in the item | `text.quote_found` | the run's `[text]` | looser | stricter |
| Injection scan | `text.injection_flags` | built-in | can break a match: looser | can break a match: looser |
| Re-roll: the same text judged again | `brief._check_loop`, `text_hash` | built-in | stricter | looser: a typographic variant passes |
| The control is identical to the candidate | `brief.write_briefs` | built-in | stricter | looser: a copy passes as the control |
| The previous version or the baseline rides along | `brief.write_briefs` | built-in | left out more often | shown more often |
| A judge gave one quote for every dimension | `record.check_output` | built-in | flagged more often | flagged less often: looser |
| The transcript holds the whole brief | `record.record_reply` | built-in | looser: an edited brief passes | stricter |
| A void needs a brief that was never pasted | `record._void` | built-in | stricter | looser: a pasted brief is voided |

## `[injection]`

| Key | Default | Bounds | Meaning |
|---|---|---|---|
| `patterns` | five expressions | every default pattern must stay; more may be added; each a valid regular expression that matches its sample | Run on text normalised with the built-in `[text]` settings, whatever the configuration says. A match in the candidate is flagged in the author's findings and in the report. A match in the spec's title, purpose, persona descriptions or declared choices refuses `init`. One default pattern is French, for texts written in French. |
| `samples` | one line per default pattern | one per pattern, in the same order | A line each pattern must match after the scan's normalisation, the built-in one (default punctuation map and stripped characters, whitespace, case): a pattern holding a character that normalisation removes or maps can never match, and is refused. A pattern that matches nothing would switch the check off, so it is refused. |

## `[transcript]`

A transcript is a JSONL file, one JSON object per line. The reply is the text of the last
entry whose `role_key` equals `assistant_role` and whose message holds at least one non-empty
text block. The prompt is the first entry whose `role_key` equals `user_role`; its content may
be a string or a list of text blocks, and it must contain the whole brief file. Lines that are
not JSON, or entries of another shape, are skipped. If no reply is found, `record` refuses
rather than guess.

```json
{"type": "user", "agentId": "a1", "message": {"content": "<the brief, verbatim>"}}
{"type": "assistant", "agentId": "a1", "message": {"content": [{"type": "text", "text": "..."}]}}
```

| Key | Default | Bounds | Meaning |
|---|---|---|---|
| `role_key` | `"type"` | not empty | Entry field holding the role. |
| `assistant_role` | `"assistant"` | not empty | Role value of the judge's entries. |
| `user_role` | `"user"` | not empty | Role value of the prompt entry. |
| `message_key` | `"message"` | not empty | Entry field holding the message. |
| `content_key` | `"content"` | not empty | Message field holding the content. |
| `block_type_key` | `"type"` | not empty | Block field holding the block type. |
| `text_block_type` | `"text"` | not empty | Block type that carries text. |
| `text_key` | `"text"` | not empty | Block field holding the text. |
| `agent_id_key` | `"agentId"` | not empty | Field holding the agent id. When present, it must equal `--agent-id`. |
| `handback_tool` | `"SubagentHandback"` | | Some agent runtimes return a subagent's answer as a tool call rather than as a final text. Name that tool here and the message of its last call is read as the reply, before any text. A runtime without this tool is unaffected. Empty means no tool is read; a run frozen earlier keeps the value it has. A call whose message is not one non-empty string is recorded as a rejected, malformed reply. |
| `tool_block_type` | `"tool_use"` | not empty | Block type of a tool call. |
| `tool_name_key` | `"name"` | not empty | Block field holding the tool name. |
| `tool_input_key` | `"input"` | not empty | Block field holding the call's input. |
| `tool_message_key` | `"message"` | not empty | Input field holding the returned message. |
| `tool_id_key` | `"id"` | not empty | Field holding a tool call's id. |
| `tool_result_type` | `"tool_result"` | not empty | Block type of a tool result, in a user entry. |
| `tool_result_id_key` | `"tool_use_id"` | not empty | Tool result field naming the call it answers. |
| `tool_error_key` | `"is_error"` | not empty | Tool result field set to true when the runtime refused the call. A refused handback was never delivered, so the reader prefers a delivered handback, then the last assistant text; only when nothing was delivered at all is the last refused handback the reply (a malformed one is recorded as rejected). For `--void`, a refused handback still counts as a reply. |

`record --void` fails closed: any non-empty assistant text and any tool call, whichever its
name, counts as a reply, because a runtime may deliver the answer through a tool no setting
names. A judge whose prompt held the whole brief and that left any of those cannot be voided.

## Why each section is a setting

- `[run]`: the per-run choices a commissioner makes (panel size, round limit, calibration
  limits, stances); they are also spec keys, and frozen into the spec.
- `[limits]`: the floors of those choices; an organisation may want them stricter.
- `[severity]`: how much a flaw costs; bounded so it can only cost more.
- `[quotes]` and `[text]`: how strict the quote check is, and which typography it forgives. The
  quote floors are the defaults, so quotes can only be made stricter. `[text]` decides only
  whether a judge's quote is found: every other check, the injection scan included, uses the
  built-in normalisation (see the table under `[text]`), so an override can loosen none of them.
  The settings are bounded: `strip_chars` is held at the default set, and a punctuation mapping
  turns one punctuation character into another, never deleting one, splitting a word or joining
  two (a run of mapped and literal dots that normalises to an ellipsis reads as one, which a
  judge could write as `...` anyway). They are listed as overrides in the report header and in
  `status`.
- `[injection]`: texts in other languages need more patterns; the defaults always stay.
- `[transcript]`: agent runtimes write transcripts in different shapes; these keys adapt the
  reader without touching the checks.
- `[judge]`: which program judges, how long it may take, and which credentials it may see,
  depend on the machine and the model; they are frozen so a run keeps one judge.

## `[judge]`

The judge runner (`sealed-panel judge`, see [cli.md](cli.md)). Set it in the file given to `init
--config`, or, for the MCP server, in the file given to `sealed-panel mcp --config`, outside its
root: the server reads that file once, freezes it into every run it starts and serves only runs
frozen with it, so the caller of a tool never picks its judge. It is frozen into the run like
every other setting, so the judges cannot change in the middle of a run, and a command that
differs from the default (empty) is listed in full next to the grade. A run with a judge command
takes runner replies only.

```toml
[judge]
command = ["python", "/path/to/my_judge.py"]
timeout_s = 300
env_allow = ["MY_MODEL_API_KEY"]
```

| Key | Default | Bounds | Meaning |
|---|---|---|---|
| `command` | `[]` | a list of non-empty strings | The judge command as an argument list, started once per brief without a shell. It reads the brief on stdin and prints the reply on stdout. Empty means host mode: your agent host runs the judges and `record` stores their replies. The list is stored with every reply and shown in the report: never put a credential in it. |
| `timeout_s` | `300` | 1 to 3600 | Seconds a judge process may run. Past it, the process and every process it started are stopped, and the attempt is recorded as void. |
| `env_allow` | `[]` | environment variable names | Variables passed to the judge process besides `PATH`, `HOME` and `LANG` (each only when set). |
| `max_reply_bytes` | `1048576` | 1024 to 16777216 | Bytes a judge may write to stdout. Past it, the process is stopped and the attempt is recorded as void; only the bytes up to the cap are kept. |

The judge process never inherits the environment of the process that runs sealed-panel. That
environment often holds credentials (cloud tokens, repository tokens, other API keys) that a
judge has no use for, and a judge is a program that reads text written by someone else. So it
gets `PATH`, `HOME` and `LANG`, the names you list in `env_allow`, and nothing else: a key
reaches a judge only when you named it. The names passed are stored with each reply; their
values never are.

A run frozen before 0.3.0 has no `[judge]` section and is in host mode.

## `[mcp]`

| Key | Default | Bounds | Meaning |
|---|---|---|---|
| `root` | `""` | absolute or starting with `~` | The MCP server's root, read from the file given to `sealed-panel mcp --config`: every path a tool takes must resolve under it, symbolic links followed. Empty means the directory the server starts in; `--root` overrides it. It places files and changes no grade, so it is not listed among the settings that differ from the defaults. |

## `[library]`

| Key | Default | Bounds | Meaning |
|---|---|---|---|
| `path` | `""` | absolute or starting with `~`; never inside the package, inside a run, nor inside a git working tree that does not ignore it | The user rubric library. Empty means the user-level default (`~/.local/share/sealed-panel/rubrics`, or the platform equivalent), never the current directory. `SEALED_PANEL_RUBRICS` overrides it, `~` is expanded, and `init` freezes the resolved path into the run; it is not listed among the settings that differ from the defaults. See [rubrics.md](rubrics.md). |
| `learn` | `true` | | After a sealed panel aggregates, write learned candidates to the library inbox. |
| `control_min_candidate` | `4.0` | 1 to 5 | `control_too_close` fires only when the sealed candidate's mean raw score on the dimension is at least this. |
| `control_gap` | `1.0` | 0 to 4 | ... and the control's mean raw score is within this of it. |

## What stays in code

Some values are part of the instrument or of the protocol, not settings, and stay in the code
as named constants:

- the brief texts (header, rubric heading, judging steps, output instructions), the item tag,
  the label pool, and the default 2 and 4 anchors (`brief.py`): they are the instrument, and
  every run with the same version of sealed-panel asks the judges the same thing;
- the size of the brief id (6 random bytes) and of the shuffle seed (64 bits) (`brief.py`);
- the cap of 3 void attempts per judge slot and round (`record.py`);
- the judge runner's base environment (`PATH`, `HOME`, `LANG`), the size of its agent ids (6
  random bytes), the grace period after it stops a process (5 seconds), the size of a read
  (64 KiB), and the margin past the timeout after which an in-progress marker whose runner
  still runs counts as stale (60 seconds) (`runner.py`);
- the length above which the report shows a value by hash (80 characters) (`config.py`);
- the score scale, integers 1 to 5, with 5 as the convergence target, and the required anchors
  1, 3 and 5 (`decide.py`, `spec.py`);
- the stop rule's window of two rounds and the suspect-jump threshold of 2 (`decide.py`);
- the decision names, the run directory layout and its file names;
- widths and scan sizes: the text hash (16 hex characters), file-name parts of recorded
  replies, the rubric hash in the report (12), the characters searched for an agent id, and
  the largest file read when looking for unrecorded agents;
- the file extensions that make a `facts` text look like a path (`spec.py`);
- the learning thresholds (two loop judges for a new dimension, two judges of one round to
  sharpen an anchor) and the suggest weights (`learn.py`, `library.py`);
- rounding of stored statistics, and the error, findings and report wording.
