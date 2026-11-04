# Rubric library

A rubric is the part of a run that takes the most care to write and that improves the most
with use. The library gives a starting rubric for common kinds of text, and collects what
sealed panels reveal about a rubric as candidates that a person can promote or reject. Nothing
changes a template on its own.

## Built-in templates

sealed-panel ships eight generic templates as package data in `src/sealed_panel/rubrics/`:

| id | for |
|---|---|
| `cv` | a CV or resume for one role |
| `cover-letter` | a letter sent with an application |
| `pitch` | a short pitch asking a decision maker for a next step |
| `message` | an email or chat message that asks for an action by a date |
| `essay` | a text that defends one thesis |
| `interview-answer` | a spoken answer to one interview question |
| `plan-doc` | a design doc, plan or proposal asking for a decision |
| `prompt-output` | the answer a model gave to a prompt you are tuning |

## Template format

A template is one JSON object with these keys:

| Key | Meaning |
|---|---|
| `id` | Lowercase letters, digits and dashes. A user template with the same id overrides the built-in. |
| `version` | An integer from 1, bumped by every promotion. |
| `title` | A readable name. |
| `kinds` | The kinds of text it is for, such as `email` or `chat`. |
| `aliases` | Optional. Other names for those kinds, such as `cover letter` or `e-mail`. |
| `keywords` | Words that break ties between templates of the same kind. |
| `when_to_use` | One sentence. |
| `facts_hint` | Optional. What to paste as facts, shown in the skeleton. |
| `personas` | Placeholder personas (`id`, `description`) to adapt for each run. |
| `dimensions` | 3 to 6 dimensions, each with exactly `id`, `name`, `question` and `anchors` at `"1"`, `"3"` and `"5"`, written as observable evidence on the page. |
| `measured` | Suggested instrument criteria, in the spec's `measured` format. |
| `provenance` | A list of `{date, source, change}`, one entry per version. |
| `based_on` | Optional, written by promotion: `{version}` of the built-in a user template was copied from. |

A template never holds a TODO marker. The format is checked by code every time a template is
loaded. A user template that cannot be read or is invalid is skipped with a warning, and the
built-in of the same id is served instead.

## Suggest

```
sealed-panel rubrics suggest --kind "cover letter" [--text draft.md] [--top 3]
```

The kind is compared after folding case and turning spaces and underscores into dashes, so
`Cover Letter`, `cover_letter` and `cover-letter` are the same kind. Only templates whose kinds
or aliases match are returned, each with its `when_to_use` and its table of dimensions.
Keywords found as whole words in the text only order templates of the same kind; ties go by
id. With no match, the command says so and lists every template with its `when_to_use`.

## Use

```
sealed-panel rubrics use message --out spec.json [--persona-file personas.json]
```

Writes a spec skeleton: the template's dimensions, its suggested instruments, and
`template: {id, version, sha256}`, where `sha256` is a hash of the dimensions; `init` adds
`modified`. TODO markers stand for the title, the purpose, the facts (with the template's
`facts_hint`), every suggested instrument threshold and, without `--persona-file`, the persona
descriptions. `init` refuses the spec until every marker is replaced. A `facts_file` put in
place of `facts` is read relative to the spec file's folder.

At `init`, the template reference is checked against the library: an unknown id, or a version
that is neither the served one nor the built-in one, is refused. `sha256` is recomputed from
that template's dimensions, and `modified` records whether the spec's dimensions still match
them; neither is taken from the spec. Both are part of the rubric hash, and the report header
shows a modified template. A run holds a copy of the dimensions, not a link to the template: a
later promotion never changes a frozen run.

## The user library

The user library is a user-level directory, never relative to the current directory:
`~/.local/share/sealed-panel/rubrics` on Linux (or `$XDG_DATA_HOME/sealed-panel/rubrics`),
`~/Library/Application Support/sealed-panel/rubrics` on macOS, `%APPDATA%\sealed-panel\rubrics`
on Windows. The `SEALED_PANEL_RUBRICS` environment variable or the `[library] path` setting
replaces it, and `--library DIR` replaces it for one command. `~` is expanded everywhere. The
environment variable and the setting must be absolute (or start with `~`); a flag may be
relative to the current directory and is made absolute.

Wherever it comes from, a library is refused when it lies inside the installed package, or
inside a git working tree that does not ignore it: no candidate is ever written where it
could be committed by accident.

`init` freezes the resolved library into the run. `aggregate` writes the run's candidates
there and prints the path. `rubrics ... --run RUN` reads the library frozen into that run. A
run frozen by 0.1.0 has no library in its configuration and uses the one resolved at the time
of the command.

```
<library>/
  message.json        a user template (overrides the built-in message)
  inbox/c-....json    open candidates
  history/c-....json  promoted or rejected candidates
```

**The user library is private by nature.** Candidates quote the texts that were judged, word
for word, the user's own notes, and the run directory. Keep the library out of any public
repository. Learned candidates never go into the package.

## Candidates

After a sealed panel aggregates, sealed-panel reads the run and writes candidates to the inbox,
unless `[library] learn` is false. Learning never fails a seal: an error is reported as
"learning skipped" in aggregate's output, and `sealed-panel learn RUN` tries again. Learning a
run again adds nothing that is already recorded.

| Kind | Written when |
|---|---|
| `judges_disagree` | In the sealed panel, a dimension's valid judges' scores spread by 2 or more, or a judge's severity contradicts its score before capping: a score above the run's cap for a blocking or material flaw (a 4 or 5 with blocking, a 5 with material by default), or a 1 with none. |
| `control_too_close` | In the sealed panel, the candidate's mean raw score on a dimension is at least `[library] control_min_candidate` (4 by default) and the control's mean raw score is within `control_gap` (1) of it, both before severity caps. A weak text does not trigger it: a low text next to a low control says nothing about the dimension. Loop rounds do not count. See below for when it can fire. |
| `user_disagreement` | The user records their own read with `sealed-panel feedback RUN --note TEXT [--dimension ID]`, after the seal. |

`control_too_close` depends on how many dimensions the persona scores: the rubric's
dimensions, less those restricted to other personas. Calibration bounds each judge's control
mean over the persona's dimensions at the run's `control_max` (2 by default), not its score on
one dimension. With n such dimensions and every other score at the floor of 1, a calibrated
judge can give the control at most `n * control_max - (n - 1)` on one dimension, and the signal
needs the control's mean there to be at least `control_min_candidate - control_gap` (3 by
default). With the defaults, a persona with 1 dimension allows at most 2, so the signal cannot
fire. A persona with 2 dimensions allows at most 3, so it fires only when every valid judge of
the persona scores the control 3 on that dimension and 1 on the other, and the candidate's
mean on it is exactly 4. From 3 dimensions (at most 4) it has room. A persona limited to 2 of a
rubric's 3 dimensions counts as 2.

Each candidate is a JSON file named by its id, with `kind`, `status` (`open`), `template` (the
run's template reference), `dimension`, `run` (rubric hash, template, run directory), `runs`
(every run that showed the signal), `evidence` (round, judge numbers, each judge's quote with
whether code verified it, notes), `count`, and `proposed_change`. The id is the file name,
checked against a fixed pattern; an `id` field inside the file is never used.

Candidates are de-duplicated by kind, template id, version and whether the run modified the
template (or, without a template, the run's rubric hash) and dimension, ignoring case: the
same signal adds its new evidence and run to the open candidate. A closed signal is not
reopened for that template version: neither a rejected one nor a promoted one, so learning a
run on the old version again changes nothing. If a rejected signal recurs on a newer version,
the new candidate shows "rejected before" with the earlier reason.

## Promotion

A proposed change is one of:

- `add_dimension` with a full `dimension` object;
- `rewrite_anchors` with the `dimension` id and the new `anchors` (merged over the old ones);
- `remove_dimension` with the `dimension` id.

Learned changes hold TODO markers where a person must write the new question or anchors.
Promotion only checks the fields its action uses, so changing a rewrite into
`remove_dimension` does not require filling the unused anchors.

To promote:

1. read the candidate in `inbox/`, and its evidence;
2. write or edit its `proposed_change` (an agent drafts one only when the user asks);
3. run `sealed-panel rubrics promote CANDIDATE_ID [--into TEMPLATE_ID]` yourself.

`promote` prints the current and the proposed text side by side and the file it will write. In
a terminal it asks you to type the candidate id; without a terminal it refuses unless `--yes`
is given. It refuses a change that still holds markers in the fields it uses, a change that
would break the template format, and a user file for that id that cannot be read (fix or
remove it first). It writes the file that serves the template (a user file of that id, or a
new `<id>.json`; a `<id>.json` holding a template of another id is never overwritten), copying
a built-in first and recording it in `based_on`; bumps the version above both the user and the
built-in version; appends a provenance entry naming the candidate; and moves the candidate to
`history/`. A history file is never overwritten, even an unreadable one: its id stays reserved,
and a new candidate takes the next id. Both files are staged and then moved into place; if the
second move fails, the first is rolled back, and no staged file is left behind, so a failure
leaves the template and the inbox as they were. At the prompt, an empty answer or the end of
input counts as "not confirmed". Built-in templates never change.
`rubrics list` flags a user template whose built-in is newer than the version it was based on.

To close a candidate without a change: `sealed-panel rubrics reject CANDIDATE_ID --reason
TEXT`.
