# Development instructions

- Update `CHANGELOG.md` in the same change whenever user-visible behavior changes or a bug is fixed.
- Keep changelog entries to one sentence each, group them by date with the newest first, and describe verified changes without investigation notes.
- Run `python3 -B -m unittest -v` before committing Python changes.
- Keep Python bytecode out of the SwiftBar plugin folder and use `python3 -B` or `PYTHONDONTWRITEBYTECODE=1` when importing an installed plugin for verification.
