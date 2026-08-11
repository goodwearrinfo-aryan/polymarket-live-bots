import os, json, sys, subprocess
os.environ["PYTHONWARNINGS"] = "ignore"

result = subprocess.run([
    'curl', '-s',
    'https://gamma-api.polymarket.com/markets?active=true&limit=200&order=createdAt&ascending=false'
], timeout=5, capture_output=True, text=True)

try:
    data = json.loads(result.stdout)
    markets = data.get('data', []) if isinstance(data, dict) else data
    
    candidates = []
    for m in markets:
        if not m.get('question'):
            continue
        outcomes = m.get('outcomes', [])
        prices = m.get('outcomePrices', [])
        
        # Ensure prices are numeric
        try:
            prices = [float(p) if isinstance(p, (int, float)) else 0 for p in prices]
        except:
            prices = []
        
        if len(outcomes) >= 2 and len(prices) >= 2:
            candidates.append({
                'id': m.get('id'),
                'question': m['question'],
                'outcomes': outcomes,
                'prices': prices,
                'endDate': m.get('endDate'),
            })
    
    clusters = {}
    for c in candidates:
        q_lower = c['question'].lower()
        
        if 'trump' in q_lower or 'election' in q_lower or 'senate' in q_lower:
            k = 'politics'
        elif 'bitcoin' in q_lower or 'ethereum' in q_lower or 'btc' in q_lower or 'eth' in q_lower:
            k = 'crypto'
        else:
            k = 'other'
        
        if k not in clusters:
            clusters[k] = []
        clusters[k].append(c)
    
    print("=== LOGICAL ARB CANDIDATE CLUSTERS ===")
    for topic in ['politics', 'crypto', 'other']:
        if topic not in clusters:
            continue
        mkt_list = clusters[topic]
        if len(mkt_list) < 2:
            continue
        
        print(f"\n{topic.upper()}: {len(mkt_list)} markets")
        for i, m in enumerate(mkt_list[:3]):
            print(f"  [{i}] {m['id']} | {m['question'][:70]}")
            print(f"      Outcomes: {m['outcomes']}")
            prices_str = ', '.join([f"{o}={p:.2f}" for o, p in zip(m['outcomes'], m['prices'])])
            print(f"      Prices: {prices_str}")
    
    # Save candidates for LLM probe
    with open('_candidates.json', 'w') as f:
        json.dump(clusters, f, indent=2)
    
    print(f"\n\nSaved {sum(len(v) for v in clusters.values())} candidates to _candidates.json")
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
