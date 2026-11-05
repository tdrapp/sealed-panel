# Spec reference

A spec is one JSON object. Unknown keys are refused at every level. `spec.example.json` beside
this file is a complete example.

## Top level

| Key | Required | Type | Meaning |
|---|---|---|---|
| `title` | yes | string | Neutral name of the run. Judges see it. |
| `purpose` | yes | string | What a 5 makes happen. Judges see it. |
| `personas` | yes | list | Who reads the text (see below). |
| `dimensions` | yes | list | What is scored (see below). |
| `facts` | no | string | The facts the judges may trust, as text. Refused as a path (use `facts_file`) when, after trimming spaces, it starts with `~/`, `./` or `../`; or starts with `/` and holds another `/`; or is one token holding a `/`; or is one line ending in `.md`, `.txt`, `.json`, `.yaml`, `.yml`, `.pdf`, `.csv`, `.html`, `.htm`, `.doc` or `.docx` (any case). |
| `facts_file` | no | string | A path to a file holding the facts, read and frozen at `init`; a relative path is read from the spec file's folder. Not together with `facts`. |
| `measured` | no | list | Criteria an instrument measures instead of a judge. |
| `declared_choices` | no | list | What the commissioner asked for on purpose. |
| `judges_per_persona` | no | integer | Default 3, between 2 and 9. |
| `max_rounds` | no | integer | Default 6, at least 1. |
| `control_max` | no | number | Default 2.0, between 1 and 2.5. |
| `good_min` | no | number | Default 4.0, between 3.5 and 5. |
| `noise_floor_min` | no | number | Default 0.5, between 0.25 and 1. |
| `framings` | no | list of strings | Reading stances; the packaged three by default. |
| `template` | no | object | `{id, version, sha256}` of the template the spec started from, written by `rubrics use`; `init` adds `modified`. `init` refuses an id or version the library does not know, recomputes `sha256` from that template, and sets `modified` to whether the dimensions differ from it; a value given in the spec for either is replaced. Part of the rubric hash. |

A string holding `TODO(sealed-panel)` is refused by `init`: `rubrics use` leaves these markers
where the spec needs your words.

## A persona

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | Unique, short. Used in the report (`em/relevance`). |
| `description` | yes | Who they are and what they decide. Judges see it. |

## A dimension

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | Unique, short. |
| `name` | no | A readable name. |
| `question` | yes | What the judge answers. |
| `anchors` | yes | An object with `"1"`, `"3"` and `"5"` (required) and `"2"`, `"4"` (optional), each written as evidence on the page. Without them, 4 reads "the 5 anchor except one material gap" and 2 "between 1 and 3". |
| `weight` | no | A finite number above 0, default 1. Used only by the informational composite. |
| `personas` | no | Persona ids the dimension applies to; all personas by default. |

## A measured criterion

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | A unique string, used with `measure --id`. |
| `description` | yes | A string, shown to judges as not theirs to score. |
| `max`, `min` | no | Finite numbers, `min` not above `max`. With one, the value decides pass or fail; without, `--pass` or `--fail` is required. |

## A declared choice

| Key | Required | Meaning |
|---|---|---|
| `choice` | yes | What was done on purpose. Judges are told not to penalise it as such. |
| `user_said` | yes | The commissioner's own words. The author cannot exempt a weakness by declaring it. |
