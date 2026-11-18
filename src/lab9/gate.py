"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import math
import numpy as np

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item
from lab9.stats import paired_bootstrap


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    blocks = []
    pr_rows = pr.get("rows", [])
    base_rows = base.get("rows", [])
    
    pr_trials = {(r.get("item_id"), r.get("seed")) for r in pr_rows}
    base_trials = {(r.get("item_id"), r.get("seed")) for r in base_rows}
    
    if pr_trials != base_trials:
        blocks.append("INTEGRITY: Tập (item_id, seed) bị thay đổi so với baseline.")
        
    if pr.get("evaluator_failures"):
        blocks.append("INTEGRITY: Tồn tại evaluator_failures trong báo cáo PR.")
        
    for row in pr_rows:
        if any(v is None for k, v in row.items() if k not in ["item_id", "seed", "slices"]):
            blocks.append(f"INTEGRITY: Phát hiện giá trị NaN/None ở item {row.get('item_id')}.")
            break
            
    if base_spec:
        pr_metrics = {m["id"]: m for m in spec.get("metrics", [])}
        base_metrics = {m["id"]: m for m in base_spec.get("metrics", [])}
        
        for m_id, b_m in base_metrics.items():
            if m_id in pr_metrics:
                p_m = pr_metrics[m_id]
                # Nếu metric là blocking, không cho phép ngưỡng min mới thấp hơn ngưỡng min cũ
                b_min = b_m.get("threshold", {}).get("min")
                p_min = p_m.get("threshold", {}).get("min")
                if b_m.get("blocking") and b_min is not None and p_min is not None:
                    if p_min < b_min:
                        blocks.append(f"INTEGRITY: Ngưỡng {m_id} bị nới lỏng ({p_min} < {b_min}).")
                        
    if len(pr_rows) != len(base_rows):
        blocks.append("INTEGRITY: Số lượng dòng không khớp.")

    pr_metrics_dict = pr.get("metrics", {})
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in pr_metrics_dict.values()):
         blocks.append("INTEGRITY: Có giá trị NaN hoặc None trong metrics tổng.")

    return blocks


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocks = []
    lines = []
    
    spec_slices = spec.get("slices", [])
    pr_rows = pr.get("rows", [])
    base_rows = base.get("rows", [])
    
    for slice_def in spec_slices:
        slice_name = slice_def.get("name")
        m_id = slice_def.get("metric")
        min_threshold = slice_def.get("min")
        is_blocking = slice_def.get("blocking")
        check_drop = slice_def.get("no_significant_drop")
        
        pr_slice = [r for r in pr_rows if slice_name in r.get("slices", [])]
        base_slice = [r for r in base_rows if slice_name in r.get("slices", [])]
        
        if not pr_slice:
            continue
            
        # Lấy điểm và tính trung bình (mean) cho PR
        pr_scores = [float(r[m_id]) for r in pr_slice]
        mean_val = np.mean(pr_scores)
        
        block_id = f"slice:{slice_name}/{m_id}"
        
        # 1. Kiểm tra ngưỡng sàn (min)
        if min_threshold is not None and mean_val < min_threshold:
            if is_blocking:
                blocks.append(block_id)
            lines.append(f"  {block_id:<22} dưới sàn ({mean_val:.3f} < {min_threshold})")
            continue
            
        # 2. Kiểm tra sụt giảm đáng kể (nếu được yêu cầu)
        if check_drop and len(pr_slice) == len(base_slice):
            base_scores = [float(r[m_id]) for r in base_slice]
            _, ci_low, ci_high = stats.paired_bootstrap(pr_scores, base_scores)
            
            if ci_high < 0:
                if is_blocking:
                    blocks.append(block_id)
                lines.append(f"  {block_id:<22} rớt điểm (CI_high: {ci_high:+.3f})")
            else:
                lines.append(f"  {block_id:<22} ok")
                
    return blocks, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
