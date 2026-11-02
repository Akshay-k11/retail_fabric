# Development workflow (manual Git sync)

Fabric's built-in Git integration is blocked on our tenant, so code moves between
Fabric and GitHub by hand.

```
Fabric (ws_retail_dev) ──Download .py──► local repo (dev branch) ──review──► commit/push ──► PR dev → main
```

## Per phase

1. **Build & run** the notebooks in `ws_retail_dev` until they work.
2. **Export** each new/changed notebook: open it → *Edit* → *Download* → **Download as .ipynb**.
   Do **not** use *Python (.py)*: that export comments out `%run`, drops the `parameters`
   cell tag and lakehouse metadata, and can mangle docstrings, so it cannot be re-imported.
3. **Save** it into the matching folder, keeping the notebook name as the file name:
   `fabric/<workspace folder>/<notebook name>.ipynb`, e.g. `fabric/03_jobs/nb_02_bronze.ipynb`.
4. **Clean** outputs before committing: `py tools/clean_notebooks.py`
   (CI runs `--check` and fails if outputs are committed).
5. **Review**: ask Claude to review the changed files; fix in Fabric, re-export.
6. **Commit** to `dev` with a conventional message, e.g. `feat(silver): merge orders with DQ`.
7. **Push** and open a pull request `dev → main` on GitHub; merge after review.

## Restoring / deploying notebooks from Git

Workspace → *Import* → *Notebook* → *From this computer* → select the `.ipynb` files, then re-attach `lh_retail` if prompted.
(Use this to rebuild a workspace; promotion dev → prod uses a Fabric deployment pipeline.)

## Rules
- Fabric is where code runs; Git is the source of truth for what was reviewed.
- Never edit exported files locally without re-importing them into Fabric (or the two drift).
- Lakehouse data is not in Git — `nb_00_setup` recreates schemas and tables.
- No secrets in notebooks.
