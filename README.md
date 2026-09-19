# Playground

A collection of small Python projects, experiments, and code snippets that don't need their own repositories.

## Contents

| Project | Description |
| --- | --- |
| [`stock_watcher`](src/stock_watcher/) | Inventory items that notify stock watchers when their quantity changes. Watchers are held by weak reference, so an item never keeps a watcher alive. |
| [`commerce_platform`](src/commerce_platform/) | Products that store prices as integer cents, validating on assignment so money never round-trips through a float. |
| [`commerce_engine`](src/commerce_engine/) | Interned, immutable ISO 4217 currencies whose identity survives `copy` and `pickle`, plus a `PaymentMethod` factory that dispatches on kind without misfeeding its subclasses' `__init__`. |

Loose experiments and snippets live in `scratch/`, which is gitignored.

## Structure

```
Playground/
├── pyproject.toml
├── src/
│   └── <project>/        # one package per project
├── tests/
│   └── test_<project>.py
└── scratch/              # throwaway experiments (not committed)
```

## Setup

Requires Python 3.11+.

```sh
python -m venv .venv
.venv\Scripts\activate        # Windows (use `source .venv/bin/activate` elsewhere)
pip install -e ".[dev]"
```

## Development

```sh
pytest        # tests
ruff check .  # lint
mypy          # type check
```

## Adding a new project

1. Create a package in `src/<project>/` with an `__init__.py`.
2. Add its tests in `tests/`.
3. Re-run `pip install -e ".[dev]"` so the new package is importable.
4. Add a row to the **Contents** table above.
