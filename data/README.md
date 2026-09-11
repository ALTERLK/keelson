# Benchmark datasets

These are **not vendored** — the harness only ships a fixed 120-task ToolHop
subset at [`../bench/toolhop_subset.json`](../bench/toolhop_subset.json), which is
enough to reproduce every number in `bench/REPORT.md`.

To run against the full sets, fetch them here:

| dir | source |
|-----|--------|
| `ToolHop/`  | https://huggingface.co/datasets/bytedance-research/ToolHop |
| `GAIA/`     | https://huggingface.co/datasets/gaia-benchmark/GAIA |
| `HLE/`      | https://huggingface.co/datasets/cais/hle |
| `ToolBench/`, `API-Bank/`, `RestBench/`, `WebShop/`, `ALFWorld/` | their respective upstream repos |

```bash
python scripts/fetch_data.py toolhop     # just what the bench runner needs
```
