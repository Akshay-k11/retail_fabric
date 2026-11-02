# Retail Fabric Lakehouse

Mini end-to-end data engineering project on Microsoft Fabric (trial).

**Flow:** simulated source system → Bronze (raw) → Silver (clean, DQ) → Gold (star schema) → reporting

| Workspace | Git branch | Purpose |
|---|---|---|
| ws_retail_dev | dev | development |
| ws_retail_prod | main (via deployment pipeline) | production |

## Repo layout
```
fabric/                 notebooks exported from Fabric (Download as .ipynb, outputs stripped)
  01_config/            nb_config
  02_lib/               nb_lib_* (functions only, loaded with %run)
  03_jobs/              nb_00_setup … nb_05_validate
  04_tests/             nb_test_unit
  05_orchestration/     pipeline definition / screenshots
docs/                   design, architecture, workflow, runbook
tools/                  clean_notebooks.py (strip outputs before commit)
.github/                CI workflows (Phase 8)
```

Design: [docs/design.md](docs/design.md).

Fabric Git integration is disabled on our tenant, so notebooks are synced manually —
see [docs/workflow.md](docs/workflow.md).

## Phases
- [x] 0 Setup
- [x] 1 Requirements & design
- [x] 2 Config, logging, Delta helpers
- [ ] 3 Source generator
- [ ] 4 Bronze
- [ ] 5 Silver + data quality
- [ ] 6 Gold
- [ ] 7 Orchestration
- [ ] 8 CI
- [ ] 9 CD
- [ ] 10 Operations
