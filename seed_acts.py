import sqlite3, os, re, json, time, urllib.request, urllib.parse

# load token
env = open(os.path.expanduser('~/Documents/polymarket/.env')).read()
TOKEN = next(l.split('=',1)[1].strip() for l in env.splitlines() if l.startswith('INDIANKANOON_TOKEN'))

DB = os.path.expanduser('~/Documents/lawdb.sqlite')
UA = 'curl/7.88.1'

def ik_post(path, body):
    req = urllib.request.Request(
        f'https://api.indiankanoon.org{path}',
        data=body.encode(),
        headers={'Authorization': f'Token {TOKEN}', 'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': UA}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def strip_html(t):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', t or '')).strip()

ACTS = [
    ('Constitution of India', 'constitution india articles fundamental rights'),
    ('Information Technology Act 2000', 'information technology act 2000 sections'),
    ('Companies Act 2013', 'companies act 2013 sections'),
    ('Income Tax Act 1961', 'income tax act 1961 sections'),
    ('Consumer Protection Act 2019', 'consumer protection act 2019 sections'),
    ('Insolvency Bankruptcy Code 2016', 'insolvency bankruptcy code IBC 2016 sections'),
    ('Protection of Children Sexual Offences Act', 'POCSO act sections protection children'),
    ('Domestic Violence Act 2005', 'domestic violence act 2005 sections'),
    ('Right to Information Act 2005', 'right to information act 2005 sections'),
    ('Prevention of Corruption Act', 'prevention corruption act sections'),
]

db = sqlite3.connect(DB)
db.execute('''CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    act TEXT, chapter TEXT, section_number TEXT, title TEXT, body TEXT, source_url TEXT,
    UNIQUE(act, section_number)
)''')

per_act = {}
total_added = 0
for act_name, query in ACTS:
    print(f'\n--- {act_name} ---')
    act_count = 0
    for page in range(3):  # 3 pages = 30 results
        try:
            r = ik_post('/search/', f'formInput={urllib.parse.quote(query)}+doctypes:laws&pagenum={page}')
            docs = r.get('docs', [])
            if not docs:
                print(f'  page {page}: no docs, stopping')
                break
            print(f'  page {page}: {len(docs)} docs')
            for doc in docs:
                tid = doc['tid']
                try:
                    full = ik_post(f'/doc/{tid}/', '')
                    body = strip_html(full.get('doc', ''))
                    title = strip_html(doc.get('title', ''))
                    # Extract section number from title
                    sec_match = re.search(r'[Ss]ection\s+(\d+[A-Z]?)', title)
                    sec_num = sec_match.group(1) if sec_match else str(tid)
                    db.execute('''INSERT OR IGNORE INTO sections (act, chapter, section_number, title, body, source_url)
                        VALUES (?,?,?,?,?,?)''',
                        (act_name, '', sec_num, title, body[:5000], f'https://indiankanoon.org/doc/{tid}/'))
                    if db.execute('SELECT changes()').fetchone()[0]:
                        act_count += 1
                        total_added += 1
                        print(f'  + {title[:70]}')
                    db.commit()
                    time.sleep(0.3)
                except Exception as e:
                    print(f'  doc error {tid}: {e}')
        except Exception as e:
            print(f'  search error page {page}: {e}')
            break
    per_act[act_name] = act_count
    print(f'  => {act_count} added for {act_name}')

print(f'\n=== SUMMARY ===')
for act, n in per_act.items():
    print(f'  {n:3d}  {act}')
print(f'\nTotal added this run: {total_added}')
print('Total sections in DB:', db.execute('SELECT COUNT(*) FROM sections').fetchone()[0])
print('\nFull breakdown by act:')
for row in db.execute('SELECT act, COUNT(*) FROM sections GROUP BY act ORDER BY act'):
    print(f'  {row[1]:4d}  {row[0]}')
db.close()
