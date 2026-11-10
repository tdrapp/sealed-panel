# Plans

A large change starts from a plan in this folder, before any code: a new command or tool, a
change to a refusal or to the protocol, a new dependency or extra, a change to the brief texts,
or anything that touches more than a few files.

A plan is one file, `YYYY-MM-DD-topic.md`, with:

- the problem, and what is out of scope;
- the options considered, each with what it costs and what it gives, and the one chosen;
- the steps, each with the test or the check that shows it is done;
- the refusals it adds or changes, each with its test and its mutation;
- the docs, the CHANGELOG entry and the decision record it needs, if any.

The plan is reviewed before the work starts, and the pull request links it. When the work
lands, the plan stays as the record of why it was done that way; a choice that outlives the
change becomes a record in [docs/decisions/](../decisions/).
