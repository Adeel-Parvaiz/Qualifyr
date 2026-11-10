"""Print the golden-set classification report.

    .venv/Scripts/python.exe scripts/golden_eval.py

Pure and offline: it scores the BUYER/VENDOR/UNKNOWN gate against the hand-labelled rows in
tests/golden/golden_leads.jsonl. Use it to check a change did not regress classification
before shipping, and to watch accuracy against the CEO's >= 80% target as the set grows."""

from gtm_engine.eval.golden import evaluate, format_report, load_golden


def main() -> int:
    report = evaluate(load_golden())
    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
