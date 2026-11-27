# Contributing to Sniff

Thanks for helping. Sniff reads people's private documents, so correctness and privacy come first.

## How work flows

1. **Start from an issue.** Pick one, or open one so we can agree on the approach before you write code. Issues labelled `good first issue` are small and self-contained.
2. **Branch** from `main`: `<type>/<issue-number>-<short-name>`, for example `fix/42-locked-files`.
3. **Commit in small steps.** Each commit does one thing, builds, and passes the tests. Use [Conventional Commits](https://www.conventionalcommits.org/): `feat(search): …`, `fix(ask): …`, `docs: …`. Start the message body with one plain sentence on why the change matters to a user.
4. **Open a pull request** with `Closes #<issue>`. Keep it focused; split big changes into several PRs.
5. CI runs the backend tests on Windows, macOS and Linux and builds the frontend. A maintainer reviews, then merges.

## Running it

See [docs/INSTALL.md](docs/INSTALL.md), including "run from source" for macOS and Linux.

```
cd backend && uv sync && uv run pytest -q
cd frontend && npm ci && npm run build
```

## Ground rules

- **No real personal documents** in issues, tests or screenshots. Use the synthetic generator in `data/synth/`; every name and ID number there is made up.
- **Code does the maths.** Totals, dates and comparisons come from code, never from a language model.
- **Nothing leaves the laptop** unless the user turned that feature on. Say in the PR if your change sends anything anywhere.
- UI changes need before and after screenshots, in light and dark mode.

## Releases

Maintainers tag `vX.Y.Z` on `main`. CI then builds the Windows, macOS and Linux installers into a draft release with notes from the merged PRs.
