import os, json, sys, subprocess
os.environ["PYTHONWARNINGS"] = "ignore"

result = subprocess.run([
    'curl', '-s',
    'https://gamma-api.polymarket.com/markets?active=true&limit=100&order=createdAt&ascending=false'
], timeout=5, capture_output=True, text=True)

try:
    data = json.loads(result.stdout)
    markets = data.get('data', []) if isinstance(data, dict) else data
    print(f"Fetched {len(markets)} active markets")
    
    candidates = []
    for m in markets[:50]:
        if not m.get('question'):
            continue
        outcomes = m.get('outcomes', [])
        prices = m.get('outcomePrices', [])
        
        if len(outcomes) >= 2:
            candidates.append({
                'id': m.get('id'),
                'question': m['question'][:100],
                'outcomes': outcomes,
                'prices': prices,
                'endDate': m.get('endDate'),
                'resolvedBy': m.get('resolvedBy'),
            })
    
    topics = {}
    for c in candidates:
        q = c['question'].lower()
        key = 'politics' if any(x in q for x in ['trump', 'biden', 'election', 'congress']) \
          else 'crypto' if any(x in q for x in ['btc', 'eth', 'bitcoin']) \
          else 'tech' if any(x in q for x in ['ai', 'openai', 'microsoft']) \
          else 'econ' if any(x in q for x in ['inflation', 'recession', 'fed']) \
          else 'sport' if any(x in q for x in ['nfl', 'nba', 'soccer']) \
          else 'other'
        
        if key not in topics:
            topics[key] = []
        topics[key].append(c)
    
    print("\n=== TOPIC CLUSTERS ===")
    for topic, mkt_list in sorted(topics.items(), key=lambda x: -len(x[1])):
        print(f"{topic}: {len(mkt_list)} markets")
        for m in mkt_list[:2]:
            print(f"  {m['id'][:8]} | {m['question']}")
            print(f"    Outcomes: {m['outcomes'][:3]}")
    
    print(f"\nTotal candidates: {len(candidates)}")
    
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
