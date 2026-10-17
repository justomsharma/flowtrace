# The demo history graph (what the live demo runs on)

[← Back to the README](../README.md)

## Demo history graph (what the live demo runs on)

The full history graph (515,088 accounts, 3,641,261 transfers before Sep 9) takes about **2.7 GB** in FalkorDB.
FalkorDB Cloud's free tier has **100 MB**. Every query reads the history of only a few accounts, so the demo runs on
a reduced graph that keeps exactly that history: **25,916 accounts and 57,010 transfers, 54 MB** in FalkorDB
([`demo/history.py`](../backend/flowtrace/demo/history.py), files in `data/demo/`).

**How it is built.** Each bundled sample and the Sep 9 eval slice are uploaded into the **full** graph, and every
query is recorded while:
- every case is investigated with the rule-based and fixed policies, and its holds are planned;
- for the bundled samples (what the public demo can upload), every agent tool runs at its widest on every case
  account: traces both ways at 4 hops, plus all account tools. The LLM picks its own steps; in 63 recorded LLM
  investigations every tool call was on a case account.

The demo graph keeps:
- every history transfer of every account a tool, the tracer or the planner asked about;
- every transfer a query returned;
- every transfer a detector matched.

Hub checks only compare a count with a threshold, so for a hub (for example three bank accounts with 7K–139K
transfers) it keeps just enough transfers to stay over each threshold. Scoring needs no graph: it reads the earlier
payments of the samples' accounts (`data/demo/processed`, 502K rows) with the same model (`data/demo/model`).

**Checked against the full graph** ([`demo/verify.py`](../backend/flowtrace/demo/verify.py)). Both graphs start clean.
The samples are uploaded one after another, case memory is saved, every case is investigated, and every hold is
planned. Everything except timings is compared. The demo side also uses the demo scoring files and model.

| | Result |
|---|---|
| Cases for all 4 files (ids, accounts, payments, scores, shapes) | **identical** (63 cases) |
| Rule-based and fixed-workflow investigations, and their hold plans | **identical** for the bundled samples (44 of 44). On the eval slice, 81 of 82 match. In the other one (C024), the *case memory* step finds a *nearest escalated account* 3 hops away through history on the full graph, and not on the demo graph. No evidence, decision or hold changes. It happens only when the slice is uploaded after the samples, and the public demo can't upload the slice. |
| LLM investigations, replayed step by step on both graphs | **identical** for the bundled samples (22 of 22: evidence, decision, hold plan). On the eval slice, 37 of 41 match exactly. In the other 4, a backward trace or the case-memory lookup reaches history the demo graph doesn't keep. **No decision or hold plan differs.** |

**On FalkorDB Cloud (v4.22).** The live graph runs FalkorDB v4.22; the full graph and Docker run v6. The demo graph
takes 32 MB there. The same comparison found three differences, now fixed in the queries, with tests that run on
both versions:
- v4.22 has no undirected `shortestPath()`. The *nearest escalated account* lookup now uses `allShortestPaths` in a
  `MATCH`, and gives the same answers on v6.
- On a loop, v4.22 can return a path's transfers or accounts reversed. Loops are now put in path order first.
- Results with ties, or under a `LIMIT` with no `ORDER BY`, came back in each version's storage order. Every
  ordering now ends on a unique key.

After the fixes, the cloud graph gives the same 63 cases and the same 126 investigations as the full graph. The one
exception is the C024 case-memory step above. The read-only smoke (`graph.smoke --demo`) passes on the cloud.

So the live demo gives the same cases and investigations as the full graph for everything it can upload. The
evaluation numbers in the README and [evaluation.md](evaluation.md) come from the full graph.

```bash
cd backend
python -m flowtrace.demo.history build     # needs the full graph and data/processed (see docs/setup.md); ~3 minutes
python -m flowtrace.demo.history load      # into GRAPH_NAME (plain Cypher, works on any FalkorDB, Cloud included)
GRAPH_NAME=flowtrace python -m flowtrace.demo.verify run full.json
GRAPH_NAME=flowtrace_demo PROCESSED_DIR=<data/demo/processed + fx_rates, overview, signal_rates json> \
  MODELS_DIR=../data/demo/model python -m flowtrace.demo.verify run demo.json
python -m flowtrace.demo.verify compare full.json demo.json
python -m flowtrace.demo.reset             # clean demo state on this machine (API stopped); --url for a server
```
