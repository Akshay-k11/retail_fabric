"""Strip outputs and execution counts from exported Fabric notebooks before commit.

Why: outputs make diffs noisy, bloat the repo and can leak data into Git.
Keeps: cell sources, cell tags (e.g. 'parameters'), notebook metadata
(kernel + lakehouse dependencies), so a notebook re-imported from Git still works.

usage:  py tools/clean_notebooks.py            # clean all fabric/**/*.ipynb in place
        py tools/clean_notebooks.py --check    # exit 1 if any notebook still has outputs (for CI)
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1] / "fabric"
CELL_METADATA_KEEP = {"tags"}


def clean(nb: dict) -> dict:
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "code":
            cell["outputs"] = []
            cell["execution_count"] = None
        cell["metadata"] = {k: v for k, v in cell.get("metadata", {}).items()
                            if k in CELL_METADATA_KEEP}
    return nb


def main(check: bool) -> int:
    dirty = []
    for path in sorted(ROOT.rglob("*.ipynb")):
        original = path.read_text(encoding="utf-8")
        cleaned = json.dumps(clean(json.loads(original)), indent=1, ensure_ascii=False) + "\n"
        if cleaned != original:
            dirty.append(path.relative_to(ROOT.parent))
            if not check:
                path.write_text(cleaned, encoding="utf-8", newline="\n")
    verb = "need cleaning" if check else "cleaned"
    print(f"{len(dirty)} notebook(s) {verb}" + "".join(f"\n  {p}" for p in dirty))
    return 1 if (check and dirty) else 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv))
