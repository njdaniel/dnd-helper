Closes #

## What changed

<!-- Behavior, not files. What can the bot do now that it couldn't before? -->

## Definition of done

- [ ] `ruff check .` and `ruff format --check .` clean
- [ ] `mypy bot/engine bot/db` clean
- [ ] `pytest` green, **including a new test for what this builds**
- [ ] No secrets in the diff (scan it, don't just recall your intent)
- [ ] `.env.example` updated if config was added
- [ ] Scope matches the issue — nothing built ahead

## Manual verification

<!--
Copy the verification steps from the issue and record what actually happened.
"Should work" is not verification. If the issue has no manual steps, say so.
-->

## Notes for review

<!--
Anything you'd flag: a design decision that turned out wrong, a workaround you
weren't happy with, a follow-up worth filing. If the issue's approach didn't
survive contact with the code, say that here rather than quietly working
around it.
-->
