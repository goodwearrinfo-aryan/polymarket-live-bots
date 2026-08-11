#!/bin/bash
# Wire BRAIN knowledge graph into bot autonomous loops
# Queries run before edge-hunt, new-leg evaluation, retraction check

set -e

VAULT="$HOME/Documents/PolymarketVault"
cd "$VAULT"

# Query 1: Check graveyard before designing new edge
query_graveyard() {
    local edge_idea="$1"
    echo "🔍 Checking graveyard for: $edge_idea"
    /graphify query "Has the pattern \"$edge_idea\" been tried and killed?" 2>/dev/null | head -20
}

# Query 2: Verify payer test
query_payer() {
    local edge_idea="$1"
    echo "💰 Checking payer for: $edge_idea"
    /graphify query "Who structurally pays for $edge_idea and why?" 2>/dev/null | head -20
}

# Query 3: Is this structural or predictive?
query_structural() {
    local edge_idea="$1"
    echo "🏗️ Checking if structural vs predictive: $edge_idea"
    /graphify query "Can $edge_idea survive calibrated mids? Is it structural or predictive?" 2>/dev/null | head -20
}

# Query 4: What killed similar edges before?
query_killer() {
    local edge_family="$1"
    echo "☠️ What killed $edge_family edges?"
    /graphify query "How did $edge_family edges die in the graveyard?" 2>/dev/null | head -20
}

# Wire into edge-hunt loop (call before book-entry)
evaluate_new_edge() {
    local edge_type="$1"
    local description="$2"

    echo ""
    echo "═══════════════════════════════════════════════════════"
    echo "EDGE EVALUATION: $edge_type"
    echo "═══════════════════════════════════════════════════════"
    echo ""

    # Run all 4 queries
    query_graveyard "$edge_type"
    echo ""
    query_payer "$edge_type"
    echo ""
    query_structural "$edge_type"
    echo ""
    query_killer "$edge_type"

    echo ""
    echo "═══════════════════════════════════════════════════════"
    echo "VERDICT: Review queries above before booking"
    echo "═══════════════════════════════════════════════════════"
    echo ""
}

# Export for use in other scripts
export -f query_graveyard query_payer query_structural query_killer evaluate_new_edge

# Main: If called with args, evaluate that edge
if [ $# -gt 0 ]; then
    evaluate_new_edge "$@"
fi
