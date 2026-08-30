"""POC-01 analysis: how inconsistent is us-gaap concept tagging across our 20 filers?
Feeds configs/concept_mapping.yaml and design/01_data.md."""
import sys, json, collections
sys.path.insert(0, "src")
import yaml
from janus.ingest.sec_client import SecClient
from janus.config import CONFIGS_DIR, DATA_DIR

comp = yaml.safe_load((CONFIGS_DIR / "companies.yaml").read_text())["companies"]
c = SecClient()

# concept -> set(tickers using it), and concept -> n facts
users = collections.defaultdict(set)
counts = collections.Counter()
labels = {}
for row in comp:
    cf = c.company_facts(row["cik"])
    ug = cf["facts"].get("us-gaap", {})
    for concept, body in ug.items():
        n = sum(len(v) for v in body["units"].values())
        users[concept].add(row["ticker"])
        counts[concept] += n
        labels.setdefault(concept, body.get("label") or "")
    print(f"  {row['ticker']:6s} {len(ug):4d} us-gaap concepts")

print(f"\ntotal distinct us-gaap concepts across 20 filers: {len(users)}")
print(f"used by ALL 20: {sum(1 for k,v in users.items() if len(v)==20)}")
print(f"used by 1 filer only: {sum(1 for k,v in users.items() if len(v)==1)}")

def show(title, pats):
    print(f"\n=== {title} ===")
    hits = [(k, len(users[k]), counts[k]) for k in users
            if any(p.lower() in k.lower() for p in pats)]
    hits.sort(key=lambda x: -x[1])
    for k, nu, nf in hits[:14]:
        print(f"  {nu:3d}/20 filers  {nf:6d} facts  {k}")

show("REVENUE", ["Revenue"])
show("GROSS PROFIT", ["GrossProfit"])
show("OPERATING INCOME", ["OperatingIncomeLoss"])
show("NET INCOME", ["NetIncomeLoss", "ProfitLoss"])
show("R&D", ["ResearchAndDevelopment"])
show("INVENTORY", ["InventoryNet", "InventoryGross"])
show("CASH", ["CashAndCashEquivalentsAtCarryingValue"])
show("EPS", ["EarningsPerShare"])

(DATA_DIR / "concept_usage.json").write_text(json.dumps(
    {k: {"filers": sorted(v), "n_facts": counts[k], "label": labels[k]} for k, v in users.items()},
    indent=1))
print(f"\nnetwork={c.stats['network']} cache={c.stats['cache']}")
