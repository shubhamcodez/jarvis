"""
Observability: skills → trace logs → eval generation → auto-optimization.
- Trace: success rates, tokens, errors per run (per model).
- Evals: multi-turn cases, LLM-generated from logs; coherence/task scoring.
- Optimization: prompt/param tuning, pass@k, Bayesian/RL on logs.
- Guards: loop corruption mitigation.
"""
from .trace import trace_log, get_trace_log_path, list_traces
from .auto_loop import schedule_post_turn_observability
from .evals import EvalCase, EvalRun, load_eval_cases, save_eval_cases, append_eval_run
from .guards import check_loop_corruption
from .spans import span, list_spans, current_trace_id
from .metrics import snapshot as metrics_snapshot, load_latest as load_latest_metrics
from .struct_log import configure_struct_logging, list_recent_logs

__all__ = [
    "schedule_post_turn_observability",
    "trace_log",
    "get_trace_log_path",
    "list_traces",
    "EvalCase",
    "EvalRun",
    "load_eval_cases",
    "save_eval_cases",
    "append_eval_run",
    "check_loop_corruption",
    "span",
    "list_spans",
    "current_trace_id",
    "metrics_snapshot",
    "load_latest_metrics",
    "configure_struct_logging",
    "list_recent_logs",
]
