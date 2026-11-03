"""Runs RL_exploration.ipynb's setup + section 4b (bot imitation) headlessly -> runs/imitation.pt."""
import json
nb = json.load(open("RL_exploration.ipynb"))
src = lambda i: "".join(nb["cells"][i]["source"])
assert "def prep(d)" in src(11) and "runs/imitation.pt" in src(11)
g = {}
exec("\n".join(l for l in src(1).splitlines() if not l.startswith("%")), g)   # imports (no %magics)
for i in (4, 7, 9, 11):                                                        # data, split, model, imitation
    if i == 9:
        exec(src(6).split("i = 40")[0], g)                                     # describe_bid (used by cell 9)
    exec(src(i), g)
