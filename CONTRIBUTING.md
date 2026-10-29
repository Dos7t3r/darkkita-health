# Contributing

Use synthetic health samples only. Never attach real exports, tokens, server addresses, private chat identifiers or Apple signing material.

Run `python3 -m unittest discover -s tests -p 'test_*.py' -v` and `node tests/test_view.cjs` before proposing changes. For iOS changes, build the unsigned targets and explain any HealthKit/Shortcuts limitation that cannot be covered by fixtures.

Keep receiver and plugin date/timezone behavior consistent. New metric types need matching client conversions, receiver validation, summary/page handling and documented units.

Do not claim that queued wake events imply delivery, or that a single HealthKit value supports diagnosis. Avoid extra model calls and unbounded raw-sample injection into prompts.
