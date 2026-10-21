import json
from pathlib import Path

out_dir = Path('output')
runs = sorted([p for p in out_dir.iterdir() if p.is_dir() and (p / 'run_meta.json').exists()], key=lambda x: x.name, reverse=True)
latest = runs[0]
print('Latest run:', latest.name)

data = {'run_id': latest.name}
files = {
    'pulse': 'pulse.md',
    'fee_explainer': 'fee_explainer.md', 
    'themes': 'themes.json',
    'insights': 'insights.json',
    'approval': 'approval.json',
    'run_meta': 'run_meta.json',
    'publish_log': 'publish_log.json'
}

for key, filename in files.items():
    path = latest / filename
    if path.exists():
        if filename.endswith('.json'):
            with open(path, encoding='utf-8') as f:
                data[key] = json.load(f)
        else:
            with open(path, encoding='utf-8') as f:
                data[key] = f.read()

Path('public').mkdir(exist_ok=True)
with open('public/data.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2)

print('Generated public/data.json')
