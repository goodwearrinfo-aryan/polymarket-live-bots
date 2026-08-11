#!/bin/bash
# Build the ML training set from Polymarket history, then train the model.
# Safe & separate: does NOT touch the live trading bot. Re-runnable (resumes
# collection; each run adds more markets to the dataset, then retrains).
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )/ml"
cd "$DIR"
PY=$(command -v python3 || command -v python)

echo "=== Polymarket ML pipeline ==="
echo ""
echo "[1/2] Collecting history (this is the slow part — pulls price history per market)…"
"$PY" "$DIR/collect_history.py" --max-markets 250 --category crypto 2>&1 | tee "$DIR/pipeline.log"

echo ""
echo "[2/2] Training model…"
"$PY" "$DIR/train.py" 2>&1 | tee -a "$DIR/pipeline.log"

echo ""
echo "Done. Re-run anytime to add more data and retrain."
echo "Test a live prediction:  python3 ml/predict.py <condition_id>"
echo ""
echo "This window can be closed."
sleep 5
