"""The case report as a PDF: at most two A4 pages an AML analyst or compliance officer reads at a glance.

  page 1  the case at a glance: decision, summary, KPIs, the money flow, key findings and legitimate explanations
  page 2  actions and evidence: holds, the key payments, the STR narrative (who / what / when / where / why / how)
          and the audit trail

Built only from the case overview (case_view.build_overview: the same numbers as the case page), the
investigation's report, and the case's actions and audit entries. Read-only. Each page's content is shrunk to fit
its frame and every list is capped ("+N more"), so the report is never more than two pages. Readable in black and
white: levels, roles and holds are written out, and holds are also drawn dashed.

reportlab with the standard PDF fonts: pure Python, no system packages, no browser.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, KeepInFrame, PageBreak, PageTemplate, Paragraph,
                                Spacer, TableStyle)
from reportlab.platypus import Table as _Table

from flowtrace.api.case_view import ASSUMED, PATTERN

INK = colors.HexColor("#0f1d33")
INK2 = colors.HexColor("#46566e")
MUTED = colors.HexColor("#6b7a90")
BRAND = colors.HexColor("#1f5fd1")
BORDER = colors.HexColor("#c9d1dd")
SURFACE = colors.HexColor("#f3f5f8")
HOLD = colors.HexColor("#a86f00")
TINT = {"High": colors.HexColor("#fbe7e7"), "Medium": colors.HexColor("#fdf1d8"), "Low": colors.HexColor("#eceff4")}

PAGE_W, PAGE_H = A4
MARGIN, FOOT = 36, 44
W = PAGE_W - 2 * MARGIN

CREDIT = "Synthetic data: IBM AMLworld HI-Small (CDLA-Sharing-1.0)"
DISCLAIMER = "AI-assisted analysis; decisions are made by a person."

# The case page's names (frontend/src/lib/plain.ts TRICKS, frontend/src/lib/evidence.ts EVIDENCE).
TRICK = {"FAN-OUT": "Split & scatter", "FAN-IN": "Collect", "GATHER-SCATTER": "Collect, then scatter",
         "SCATTER-GATHER": "Scatter, then re-collect", "CYCLE": "Round trip", "BIPARTITE": "Group to group",
         "STACK": "Layered relay", "PASS-THROUGH": "Pass-through", "RANDOM": "Random hops"}
EVIDENCE = {
    "PASS_THROUGH": "Money passed straight through", "FAST_ONWARD_TRANSFER": "Fast onward transfers",
    "NEW_COUNTERPARTIES": "New counterparties", "ROUND_TRIP": "Money came back", "CYCLE": "Circular movement",
    "EQUAL_SPLITS": "Equal-sized splits", "CASH_OR_CRYPTO_EXIT": "Left in cash or crypto",
    "HIGH_RISK_RAIL": "Hard-to-trace payment type", "ACTIVITY_SPIKE": "Unusual activity spike",
    "DORMANT_THEN_ACTIVE": "Dormant account woke up", "CONVERGENCE": "Money converges on one account",
    "MANY_SOURCES": "Many senders", "NEAR_ESCALATED": "Close to an escalated account",
    "PRIOR_ESCALATION": "Escalated before",
    "ESTABLISHED_COUNTERPARTIES": "Known counterparties", "RECURRING_FLOWS": "Regular, scheduled payments",
    "SALARY_DISTRIBUTION": "Salary paid out to bills", "CONSISTENT_WITH_BASELINE": "Normal for this account",
    "PAYS_HIGH_VOLUME_RECEIVERS": "Pays merchants or lenders", "INTERNAL_TRANSFER": "Between the owner's own accounts",
    "FUNDS_STOP": "The money stops moving", "BEHAVIOUR_MEMORY": "Seen before and explained",
    "PRIOR_CLOSURE": "Closed as normal before",
}
DECISION = {"ESCALATE": "Escalate", "REVIEW": "Review", "CLOSE": "Close"}
CODES = {"US Dollar": "USD", "Euro": "EUR", "UK Pound": "GBP", "Yuan": "CNY", "Yen": "JPY", "Rupee": "INR",
         "Ruble": "RUB", "Canadian Dollar": "CAD", "Australian Dollar": "AUD", "Swiss Franc": "CHF",
         "Mexican Peso": "MXN", "Shekel": "ILS", "Brazil Real": "BRL", "Saudi Riyal": "SAR", "Bitcoin": "BTC"}
ACTION_TITLE = {"HOLD": "hold", "RELEASE": "release", "ESCALATE": "escalation", "FILE_STR": "STR filing",
                "CLOSE": "closure"}
EVENT = {"PROPOSED": "Proposed", "APPROVED": "Approved", "REJECTED": "Rejected", "PROVISIONAL": "Held provisionally:",
         "RELEASED": "Released", "WITHDRAWN": "Withdrawn", "SUPERSEDED": "Superseded", "AUTO_RELEASED": "Auto-released",
         "CASE_REUPLOADED": "Retired on re-upload:"}
ACTOR = {"agent": "Agent", "mcp": "MCP client", "system": "System", "agent (auto-hold)": "Agent (auto-hold)"}
MACHINES = set(ACTOR)

STATUS = {"flagged": "Suspicious", "trail": "Followed", "normal": "Normal"}  # the case page's payment pills
CAP_FINDINGS, CAP_EXPLAIN, CAP_HOLDS, CAP_PAYMENTS, CAP_AUDIT, CAP_NARRATIVE = 5, 3, 6, 10, 6, 1400
EDGE_LABELS = 12
MAX_PER_COL = 8  # accounts drawn in one column of the money flow


# --- text --------------------------------------------------------------------------------------

PLAIN = {"→": "->", "←": "<-", "≈": "~", "≥": ">=", "≤": "<=", "₹": "INR ", "−": "-", " ": " ", " ": " ",
         " ": " ", "‑": "-"}


def plain(s) -> str:
    """The standard PDF fonts cover Windows-1252 only: map the few other characters the text uses, drop the rest."""
    s = "".join(PLAIN.get(c, c) for c in str(s if s is not None else ""))
    return s.encode("cp1252", "replace").decode("cp1252")


def x(s) -> str:
    """Plain text, escaped for a Paragraph."""
    return escape(plain(s))


def cut(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def money(usd: float | None) -> str:
    """The case page's fmtMoney: whole dollars, compact from $10M ("$12.3M")."""
    v = float(usd or 0.0)
    if abs(v) >= 1e7:
        for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
            if abs(v) >= div:
                return f"${v / div:,.1f}".rstrip("0").rstrip(".") + suffix
    return f"-${-v:,.0f}" if v < 0 else f"${v:,.0f}"


def original(amount: float | None, cur: str | None) -> str:
    if not cur:
        return ASSUMED
    if amount is None:
        return cur
    code = CODES.get(cur, cur)
    return f"{code} {amount:,.4f}" if code == "BTC" else f"{code} {amount:,.0f}"


def short(acct_id: str) -> str:
    """The UI's shortAcct: "…" and the last 4 characters of the account number."""
    return "…" + (acct_id.split(":")[1] if ":" in acct_id else acct_id)[-4:]


def case_no(case_id: str) -> str:
    """The UI's caseNo: "BX-C003" -> "3"."""
    tail = case_id[case_id.rfind("-") + 1:]
    digits = tail.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
    return str(int(digits)) if digits.isdigit() and int(digits) > 0 else tail


def utc(ms: int | None, fmt: str = "%d %b %Y %H:%M") -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(fmt).lstrip("0") if ms is not None else ""


def span(ms: int) -> str:
    m = max(int(ms // 60_000), 0)
    d, h, mi = m // 1440, (m % 1440) // 60, m % 60
    if d:
        return f"{d} day{'s' if d != 1 else ''} {h} h" if h else f"{d} day{'s' if d != 1 else ''}"
    return f"{h} h {mi} min" if h else f"{mi} min" if mi else "under 1 min"


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def level(f: dict) -> str:
    """The case page's confidence for a finding: the measured strength of the sign, else its weight's level."""
    tag = f.get("tag")
    if tag in ("Strong sign", "Strong sign of normal"):
        return "High"
    if tag == "Moderate sign":
        return "Medium"
    if tag == "Common in normal accounts":
        return "Low"
    return f.get("confidence") or "Low"


# --- styles ------------------------------------------------------------------------------------

def _style(name, size=9, leading=None, font="Helvetica", color=INK, **kw) -> ParagraphStyle:
    return ParagraphStyle(name, fontName=font, fontSize=size, leading=leading or size * 1.3, textColor=color, **kw)


S = {
    "brand": _style("brand", 15, font="Helvetica-Bold"),
    "brand_sub": _style("brand_sub", 8, color=MUTED),
    "ref": _style("ref", 8, color=INK2, alignment=2),
    "title": _style("title", 17, 21, font="Helvetica-Bold"),
    "h2": _style("h2", 10.5, 13, font="Helvetica-Bold", spaceBefore=8, spaceAfter=3),
    "body": _style("body", 9, 12.2),
    "lead": _style("lead", 9.5, 12.5, font="Helvetica-Bold"),
    "small": _style("small", 7.5, 9.6, color=INK2),
    "meta": _style("meta", 8.2, 11, color=INK2),
    "cell": _style("cell", 7.8, 9.6),
    "cell_b": _style("cell_b", 7.8, 9.6, font="Helvetica-Bold"),
    "th": _style("th", 7.2, 9, font="Helvetica-Bold", color=INK2),
    "chip_k": _style("chip_k", 6.8, 8.5, font="Helvetica-Bold", color=MUTED),
    "chip_v": _style("chip_v", 12, 14.5, font="Helvetica-Bold"),
    "kpi_v": _style("kpi_v", 14, 17, font="Helvetica-Bold"),
    "kpi_k": _style("kpi_k", 7.5, 9.4, color=INK2),
    "badge": _style("badge", 6.8, 8, font="Helvetica-Bold", alignment=1),
}


def Table(*a, **kw) -> _Table:
    """Left-aligned, so tables stay on the margin when a page is shrunk to fit."""
    return _Table(*a, hAlign="LEFT", **kw)


def P(text: str, style: str = "body") -> Paragraph:
    return Paragraph(text, S[style])


def more(n: int, what: str) -> Paragraph:
    return P(f"+{n} more {what}", "small")


def grid(rows: list[list], widths: list[float], header: bool = True, zebra: bool = True) -> Table:
    t = Table(rows, colWidths=widths, repeatRows=0)
    cmds = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5), ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER)]
    if header:
        cmds += [("LINEBELOW", (0, 0), (-1, 0), 0.9, INK2)]
    if zebra:
        cmds += [("BACKGROUND", (0, i), (-1, i), SURFACE) for i in range(2 if header else 1, len(rows), 2)]
    t.setStyle(TableStyle(cmds))
    return t


# --- drawings ----------------------------------------------------------------------------------

class Logo(Flowable):
    """The app's mark: a brand square with a waypoints glyph."""

    def __init__(self, size: float = 26):
        super().__init__()
        self.size = size

    def wrap(self, *_):
        return self.size, self.size

    def draw(self):
        c, s = self.canv, self.size
        c.setFillColor(BRAND)
        c.roundRect(0, 0, s, s, s * 0.22, stroke=0, fill=1)
        c.setStrokeColor(colors.white)
        c.setFillColor(colors.white)
        c.setLineWidth(s * 0.07)
        pts = [(0.27, 0.70), (0.55, 0.50), (0.32, 0.28), (0.74, 0.30)]
        p = c.beginPath()
        p.moveTo(pts[0][0] * s, pts[0][1] * s)
        for a, b in pts[1:]:
            p.lineTo(a * s, b * s)
        c.drawPath(p, stroke=1, fill=0)
        for a, b in pts:
            c.circle(a * s, b * s, s * 0.085, stroke=0, fill=1)


def layers(ids: list[str], edges: list[tuple[str, str]]) -> tuple[list[list[str]], set[tuple[str, str]]]:
    """The case page's flow layout (frontend/src/lib/flowLayout.ts): back edges broken by a depth-first walk,
    longest-path layers, then one sweep down and one up to order each layer by its neighbours."""
    out: dict[str, list[str]] = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    for s, d in edges:
        if s == d or s not in out or d not in out or d in out[s]:
            continue
        out[s].append(d)
        indeg[d] += 1
    back: set[tuple[str, str]] = set()
    state: dict[str, str] = {}

    def visit(u: str) -> None:
        state[u] = "open"
        for v in out[u]:
            if state.get(v) == "open":
                back.add((u, v))
            elif v not in state:
                visit(v)
        state[u] = "done"

    for i in [i for i in ids if indeg[i] == 0] + ids:
        if i not in state:
            visit(i)
    fwd = {u: [v for v in out[u] if (u, v) not in back] for u in ids}
    preds: dict[str, list[str]] = {i: [] for i in ids}
    for u, vs in fwd.items():
        for v in vs:
            preds[v].append(u)
    pending = {i: len(preds[i]) for i in ids}
    layer: dict[str, int] = {}
    queue = [i for i in ids if pending[i] == 0]
    for u in queue:
        layer[u] = max([layer[p] + 1 for p in preds[u]], default=0)
        for v in fwd[u]:
            pending[v] -= 1
            if pending[v] == 0:
                queue.append(v)
    cols: list[list[str]] = [[] for _ in range(max(layer.values(), default=-1) + 1)]
    for i in ids:
        cols[layer[i]].append(i)

    def sweep(rng, step, nbrs):
        for l in rng:
            ref = {a: k for k, a in enumerate(cols[l - step])}
            at = {a: k for k, a in enumerate(cols[l])}

            def score(a):
                ns = [n for n in nbrs[a] if n in ref]
                return sum(ref[n] for n in ns) / len(ns) if ns else at[a]
            cols[l] = sorted(cols[l], key=lambda a: (score(a), at[a]))
    if len(cols) > 1:
        sweep(range(1, len(cols)), 1, preds)
        sweep(range(len(cols) - 2, -1, -1), -1, fwd)
    return cols, back


def fit(options: list[str], font: str, size: float, width: float) -> tuple[str, float, str]:
    """The first option that fits the width at this size; else the last one, smaller (down to 5 pt)."""
    for o in options:
        if stringWidth(plain(o), font, size) <= width:
            return o, size, font
    last = options[-1]
    return last, max(5.0, size * width / max(stringWidth(plain(last), font, size), 1)), font


HOLD_TAG = {"approved": "HELD", "provisional": "HELD (PROVISIONAL)", "proposed": "HOLD PROPOSED"}


class FlowDiagram(Flowable):
    """Accounts as boxes left to right (Sender -> Intermediary -> Destination), arrows with amounts, holds dashed."""

    def __init__(self, flow: dict, names: dict[str, str], width: float = W):
        super().__init__()
        self.nodes = flow.get("nodes", [])
        self.edges = [e for e in flow.get("edges", []) if e["src"] != e["dst"]]
        self.hidden = flow.get("hidden_accounts", 0)
        self.names = names
        self.width = width
        ids = [n["id"] for n in self.nodes]
        self.cols, self.back = layers(ids, [(e["src"], e["dst"]) for e in self.edges])
        self._fold()
        tall = max((len(c) for c in self.cols), default=1)
        self.row = min(54.0, max(26.0, 230.0 / tall))
        self.height = tall * self.row + 34 + (18 if self.back else 0)

    def _fold(self) -> None:
        """A column of more than MAX_PER_COL accounts keeps its largest (and every held one) and draws the rest as
        one "+N more accounts" box, with their payments added together."""
        by_id = {n["id"]: n for n in self.nodes}
        into: dict[str, str] = {}
        for l, col in enumerate(self.cols):
            if len(col) <= MAX_PER_COL:
                continue
            ranked = sorted(col, key=lambda a: (not by_id[a].get("hold"), -max(by_id[a]["sent"], by_id[a]["received"])))
            keep, rest = set(ranked[:MAX_PER_COL - 1]), ranked[MAX_PER_COL - 1:]
            more = f"__more{l}"
            roles = [by_id[a]["role"] for a in rest]
            by_id[more] = {"id": more, "role": max(set(roles), key=roles.count), "in_case": True, "hold": None,
                           "sent": sum(by_id[a]["sent"] for a in rest), "received": sum(by_id[a]["received"] for a in rest),
                           "label": f"+{len(rest)} more accounts"}
            into |= {a: more for a in rest}
            self.cols[l] = [a for a in col if a in keep] + [more]
        if not into:
            return
        self.nodes = [by_id[a] for col in self.cols for a in col]
        merged: dict[tuple[str, str], dict] = {}
        for e in self.edges:
            s, d = into.get(e["src"], e["src"]), into.get(e["dst"], e["dst"])
            if s == d:
                continue
            m = merged.setdefault((s, d), {**e, "src": s, "dst": d, "amount": 0.0, "count": 0, "status": "normal", "hold": False})
            m["amount"] += e["amount"]
            m["count"] += e.get("count", 1)
            m["hold"] |= bool(e.get("hold"))
            if e.get("status") == "flagged" or m["status"] == "flagged":
                m["status"] = "flagged"
            elif e.get("status") == "trail":
                m["status"] = "trail"
        self.edges = list(merged.values())
        self.back = {(into.get(s, s), into.get(d, d)) for s, d in self.back}

    def wrap(self, *_):
        return self.width, self.height

    def _name(self, a: str) -> str:
        return self.names.get(a) or f"Account {short(a)}"

    def draw(self):
        c = self.canv
        if not self.nodes:
            c.setFont("Helvetica", 9)
            c.setFillColor(INK2)
            c.drawString(0, self.height - 12, "No payments to draw.")
            return
        L = len(self.cols)
        bw = min(118.0, (self.width - 20) / L - 30) if L > 1 else 130.0
        bh = min(42.0, self.row - 9)
        gap = (self.width - bw) / (L - 1) if L > 1 else 0
        top = self.height - 6 - (18 if self.back else 0)
        tall = max(len(col) for col in self.cols)
        pos: dict[str, tuple[float, float]] = {}  # box centre
        for l, col in enumerate(self.cols):
            for k, a in enumerate(col):
                cx = bw / 2 + l * gap if L > 1 else self.width / 2
                cy = top - ((tall - len(col)) / 2 + k + 0.5) * self.row
                pos[a] = (cx, cy)

        biggest = max((e["amount"] for e in self.edges), default=1.0) or 1.0
        labelled = {(e["src"], e["dst"]) for e in sorted(self.edges, key=lambda e: -e["amount"])[:EDGE_LABELS]}
        # Labels sit where the lines are apart: near the receiver when one account pays several, near the sender
        # when several pay one.
        outs = {a: sum(e["src"] == a for e in self.edges) for a in pos}
        ins = {a: sum(e["dst"] == a for e in self.edges) for a in pos}
        labels = []
        for e in self.edges:
            (sx, sy), (dx, dy) = pos[e["src"]], pos[e["dst"]]
            c.saveState()
            flagged = e.get("status") == "flagged" or e.get("hold")
            c.setStrokeColor(INK if flagged else MUTED)
            c.setFillColor(INK if flagged else MUTED)
            c.setLineWidth(0.7 + 1.9 * math.sqrt(e["amount"] / biggest))
            if not flagged:
                c.setDash(3, 2.5)
            if (e["src"], e["dst"]) in self.back or dx <= sx:
                # Money going back left (a loop): a curve over the top of the boxes.
                x0, y0, x1, y1 = sx, sy + bh / 2, dx, dy + bh / 2
                lift = max(y0, y1) + 14 + 0.06 * abs(x0 - x1)
                p = c.beginPath()
                p.moveTo(x0, y0)
                p.curveTo(x0, lift, x1, lift, x1, y1 + 4)
                c.drawPath(p, stroke=1, fill=0)
                c.setDash()
                self._head(x1, y1, 0, -1)
                lx, ly = (x0 + x1) / 2, lift - 2
            else:
                x0, y0, x1, y1 = sx + bw / 2, sy, dx - bw / 2, dy
                c.line(x0, y0, x1 - 4, y1 - 4 * (y1 - y0) / max(x1 - x0, 1))
                c.setDash()
                n = math.hypot(x1 - x0, y1 - y0) or 1
                self._head(x1, y1, (x1 - x0) / n, (y1 - y0) / n)
                t = 0.68 if outs[e["src"]] > 1 else 0.32 if ins[e["dst"]] > 1 else 0.5
                half = min(stringWidth(money(e["amount"]) + " ×99", "Helvetica-Bold", 6.6) / 2 + 2, (x1 - x0) / 2)
                t = min(max(t, half / (x1 - x0)), 1 - half / (x1 - x0)) if x1 > x0 else 0.5  # clear of the boxes
                lx, ly = x0 + t * (x1 - x0), y0 + t * (y1 - y0)
            c.restoreState()
            if (e["src"], e["dst"]) in labelled:
                labels.append((lx, ly, money(e["amount"]) + (f" ×{e['count']}" if e.get("count", 1) > 1 else "")))
        size = 6.6 if gap - bw >= 48 or L == 1 else 5.8
        for lx, ly, text in labels:  # on top of every line
            w = stringWidth(text, "Helvetica-Bold", size) + 4
            c.setFillColor(colors.white)
            c.rect(lx - w / 2, ly - 3.2, w, size + 2.4, stroke=0, fill=1)
            c.setFillColor(INK)
            c.setFont("Helvetica-Bold", size)
            c.drawCentredString(lx, ly - 0.8, text)

        for n in self.nodes:
            cx, cy = pos[n["id"]]
            x0, y0 = cx - bw / 2, cy - bh / 2
            c.saveState()
            c.setFillColor(SURFACE if n["role"] == "Sender" else colors.white)
            if n.get("hold"):
                c.setStrokeColor(HOLD)
                c.setLineWidth(2)
                c.setDash(4, 2)
            else:
                c.setStrokeColor(INK2 if n.get("in_case") else BORDER)
                c.setLineWidth(1)
            c.roundRect(x0, y0, bw, bh, 4, stroke=1, fill=1)
            c.restoreState()
            name, sid = (n["label"], "") if n.get("label") else (self._name(n["id"]), short(n["id"]))
            letter = name.replace("Account ", "")
            role = n["role"]
            amount = money(n["received"] if n["role"] != "Sender" else n["sent"])
            verb = "Sent" if n["role"] == "Sender" else "Received"
            room = bw - 8
            if bh >= 30:
                lines = [(fit([f"{name}  {sid}", name, letter], "Helvetica-Bold", 8, room), INK, bh - 10.5),
                         (fit([role if n.get("in_case") else f"{role} (traced)", role, role[:6] + "."], "Helvetica", 7, room),
                          INK2, bh - 20.5),
                         (fit([f"{verb} {amount}", amount], "Helvetica-Bold", 7.2, room), INK, 4.5)]
                if sid and lines[0][0][0] != f"{name}  {sid}":  # the short id goes with the role when the name fills the line
                    lines[1] = (fit([f"{sid} · {lines[1][0][0]}", sid], "Helvetica", 7, room), INK2, bh - 20.5)
            else:
                lines = [(fit([f"{letter} {sid}", letter], "Helvetica-Bold", 6.8, room), INK, bh - 8.5),
                         (fit([f"{role} · {amount}", amount], "Helvetica", 6.2, room), INK2, 2.8)]
            for (text, size, font), ink, dy in lines:
                c.setFont(font, size)
                c.setFillColor(ink)
                c.drawString(x0 + 4, y0 + dy, plain(text))
            if n.get("hold"):
                tag = fit([HOLD_TAG.get(n["hold"], "HOLD"), "HOLD"], "Helvetica-Bold", 5.8, bw - 10)[0]
                w = stringWidth(tag, "Helvetica-Bold", 5.8) + 6
                c.setFillColor(colors.black)
                c.rect(x0 + bw - w - 3, y0 + bh - 3.5, w, 8, stroke=0, fill=1)
                c.setFillColor(colors.white)
                c.setFont("Helvetica-Bold", 5.8)
                c.drawString(x0 + bw - w, y0 + bh - 1.3, tag)

        self._legend(c)

    def _head(self, x1: float, y1: float, ux: float, uy: float) -> None:
        c, s = self.canv, 5.5
        px, py = -uy, ux
        p = c.beginPath()
        p.moveTo(x1, y1)
        p.lineTo(x1 - s * ux + s * 0.55 * px, y1 - s * uy + s * 0.55 * py)
        p.lineTo(x1 - s * ux - s * 0.55 * px, y1 - s * uy - s * 0.55 * py)
        p.close()
        c.drawPath(p, stroke=0, fill=1)

    def _legend(self, c) -> None:
        """Only the marks the drawing uses."""
        y, at = 4, 0.0
        c.setFont("Helvetica", 6.8)
        c.setFillColor(INK2)
        marks = [("solid", "Suspicious payment")]
        if any(not (e.get("status") == "flagged" or e.get("hold")) for e in self.edges):
            marks.append(("dashed", "Followed or normal payment"))
        if any(n.get("hold") for n in self.nodes):
            marks.append(("hold", "Account with a hold"))
        for kind, text in marks:
            c.saveState()
            if kind == "hold":
                c.setStrokeColor(HOLD)
                c.setLineWidth(1.6)
                c.setDash(4, 2)
                c.rect(at, y - 1.5, 16, 8, stroke=1, fill=0)
            else:
                c.setStrokeColor(INK if kind == "solid" else MUTED)
                c.setLineWidth(1.4)
                if kind == "dashed":
                    c.setDash(3, 2.5)
                c.line(at, y + 2.5, at + 16, y + 2.5)
            c.restoreState()
            c.drawString(at + 20, y, text)
            at += 24 + stringWidth(text, "Helvetica", 6.8) + 12
        note = "Arrows: total sent, USD equivalent (×N = number of payments)"
        if len(self.edges) > EDGE_LABELS:
            note = f"Amounts on the {EDGE_LABELS} largest flows, USD equivalent"
        if self.hidden:
            note += f". +{self.hidden} more accounts not drawn"
        c.drawRightString(self.width, y, plain(note))


# --- content -----------------------------------------------------------------------------------

def _reviewers(actions: list[dict]) -> list[str]:
    decided = sorted((a for a in actions if a.get("decided_by") and a["decided_by"] not in MACHINES),
                     key=lambda a: -(a.get("decided_at") or 0))
    return list(dict.fromkeys(a["decided_by"] for a in decided))


def _author(result: dict) -> str:
    author = ((result or {}).get("report") or {}).get("author")
    return "Rules (no AI model)" if author in (None, "template") else f"AI ({author})"


def page_one(o: dict, result: dict, actions: list[dict], now_ms: int) -> list:
    names = o.get("names") or {}
    status = o.get("status") or {}
    action = ((result or {}).get("disposition") or {}).get("action") or ""
    decision = DECISION.get(action, action.capitalize() or "Not decided")
    k = o.get("kpis") or {}
    t0, t1 = (o.get("window") or [None, None])[:2]
    reviewers = _reviewers(actions)

    head = Table([[Logo(26), [P("FlowTrace", "brand"), P("AML case report", "brand_sub")],
                   [P(f"Case reference <b>{x(o['case_id'])}</b>", "ref"), P(f"Report date {x(utc(now_ms, '%d %b %Y'))}", "ref")]]],
                 colWidths=[32, W / 2 - 32, W / 2])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("LINEBELOW", (0, 0), (-1, 0), 1.2, INK),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    out: list = [head, Spacer(1, 8),
                 P(f"Case {x(case_no(o['case_id']))} · {x(TRICK.get(o.get('typology') or '', o.get('typology') or 'Unclassified'))}", "title"),
                 Spacer(1, 6)]

    def chip(key: str, value: str) -> list:
        return [P(x(key.upper()), "chip_k"), P(x(value), "chip_v")]
    chips = Table([[chip("Decision", decision), chip("Risk level", status.get("risk_level") or "-"),
                    chip("Priority", o.get("priority") or "-"), chip("Confidence", status.get("confidence") or "-")]],
                  colWidths=[W * 0.31, W * 0.23, W * 0.23, W * 0.23])
    chips.setStyle(TableStyle([("BOX", (0, 0), (0, 0), 2, INK), ("BOX", (1, 0), (-1, 0), 0.6, BORDER),
                               ("LINEAFTER", (1, 0), (2, 0), 0.6, BORDER), ("BACKGROUND", (0, 0), (0, 0), SURFACE),
                               ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 5),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    out += [chips, Spacer(1, 5),
            P(f"<b>Investigated by:</b> {x(_author(result))} &nbsp;&nbsp;·&nbsp;&nbsp; "
              f"<b>Reviewed by:</b> {x(', '.join(reviewers[:3]) if reviewers else 'awaiting review')} &nbsp;&nbsp;·&nbsp;&nbsp; "
              f"<b>Payments:</b> {x(utc(t0, '%d %b %Y') or '-')} (UTC)", "meta")]

    # Summary: the AI's headline, then what moved, the pattern, and what decided it (3-4 lines).
    out += [P("Summary", "h2")]
    summary = o.get("summary") or {}
    if summary.get("headline"):
        out += [P(x(summary["headline"]), "lead"), Spacer(1, 2)]
    lines = list((o.get("story") or [])[:2]) + ([o["verdict_basis"]] if o.get("verdict_basis") else [])
    out += [P(x(" ".join(lines)))]

    # KPIs: the case page's four numbers, with the time span in place of the warning-sign count.
    connected, traced = k.get("connected_accounts", k.get("accounts", 0)), k.get("traced_accounts", 0)
    kpis = [(money(k.get("amount")), "Amount (USD equivalent)"),
            (str(connected), f"Accounts ({traced} found by tracing)" if traced else "Accounts in the case"),
            (str(k.get("payments", 0)), "Flagged payments"),
            (span(((t1 or 1) - 1) - (t0 or 0)) if t0 is not None else "-",
             f"Time span, {utc(t0, '%d %b %H:%M')} to {utc((t1 or 1) - 1, '%d %b %H:%M')} UTC" if t0 is not None else "Time span")]
    strip = Table([[[P(x(v), "kpi_v"), P(x(lbl), "kpi_k")] for v, lbl in kpis]], colWidths=[W / 4] * 4)
    strip.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, BORDER), ("LINEAFTER", (0, 0), (2, 0), 0.6, BORDER),
                               ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 5),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    out += [Spacer(1, 8), strip]

    out += [P("Money flow", "h2"), FlowDiagram(o.get("flow") or {}, names)]

    # Key findings, most confident first; then what could make it normal.
    rank = {"High": 0, "Medium": 1, "Low": 2}
    findings = sorted(o.get("findings") or [], key=lambda f: (rank[level(f)], -f.get("weight", 0)))
    out += [P("Key findings", "h2")]
    out += _finding_rows(findings[:CAP_FINDINGS]) if findings else [P("No indicator was found in the payment history.")]
    if len(findings) > CAP_FINDINGS:
        out += [more(len(findings) - CAP_FINDINGS, "findings on the case page")]
    explanations = o.get("explanations") or []
    out += [P("Possible legitimate explanation", "h2")]
    if explanations:
        rows = [[P(f"<b>{x(_title(f))}</b> <font size='6.8' color='#6b7a90'>{x(f.get('id', ''))}</font>", "cell"),
                 P(x(f.get("brief", "")) + (f" <font color='#46566e'>({x(f['why'].rstrip('.'))})</font>"
                                             if f.get("tag") == "Strong sign of normal" and f.get("why") else ""), "cell")]
                for f in explanations[:CAP_EXPLAIN]]
        t = Table(rows, colWidths=[178, W - 178])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER),
                               ("LINEBEFORE", (0, 0), (0, -1), 2, MUTED)]))
        out += [t]
        if len(explanations) > CAP_EXPLAIN:
            out += [more(len(explanations) - CAP_EXPLAIN, "explanations on the case page")]
    else:
        out += [P("None found in the payment history.")]
    checks = list(dict.fromkeys(f["check"] for f in findings if f.get("check")))[:3]
    if checks:
        out += [P("Verification steps", "h2")] + [P("&bull; " + x(c), "cell") for c in checks]
    return out


def _title(f: dict) -> str:
    return EVIDENCE.get(f.get("code", ""), (f.get("code") or "").replace("_", " ").capitalize())


def _finding_rows(items: list[dict]) -> list:
    rows = []
    for f in items:
        conf = level(f)
        title = _title(f)
        why = f" <font color='#46566e'>({x(f['why'].rstrip('.'))})</font>" if f.get("why") else ""
        rows.append([P(x(conf.upper()), "badge"),
                     P(f"<b>{x(title)}</b> <font size='6.8' color='#6b7a90'>{x(f.get('id', ''))}</font>", "cell"),
                     P(x(f.get("brief", "")) + why, "cell")])
    t = Table(rows, colWidths=[46, 132, W - 178])
    cmds = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER),
            ("LEFTPADDING", (1, 0), (-1, -1), 6)]
    for i, f in enumerate(items):
        cmds += [("BACKGROUND", (0, i), (0, i), TINT.get(level(f), TINT["Low"])), ("BOX", (0, i), (0, i), 0.6, INK2)]
    t.setStyle(TableStyle(cmds))
    return [t]


def _hold_status(a: dict) -> str:
    s = a.get("status")
    if s == "PROPOSED":
        return "Proposed (awaiting a decision)"
    if s == "PROVISIONAL":
        return "Provisional (auto-hold)"
    if s == "APPROVED":
        return "Approved"
    if a.get("released_at"):
        return "Released"
    return "Rejected" if s == "REJECTED" else "Withdrawn"


def _hold_until(a: dict) -> str:
    s = a.get("status")
    if s == "APPROVED" and a.get("expires_ts"):
        return utc(a["expires_ts"])
    if s == "PROVISIONAL" and a.get("provisional_until"):
        return utc(a["provisional_until"]) + " unless confirmed"
    if a.get("released_at"):
        return "Released " + utc(a["released_at"])
    if s == "PROPOSED" and a.get("expiry_days"):
        return f"{a['expiry_days']} business days after approval"
    return "-"


def _who(actor: str | None) -> str:
    return ACTOR.get(actor or "", actor or "-")


def page_two(o: dict, result: dict, actions: list[dict], audit: list[dict]) -> list:
    names = o.get("names") or {}
    nm = lambda a: names.get(a) or f"Account {short(a)}" if a else ""  # noqa: E731
    acct = lambda a: f"{nm(a)} {short(a)}" if a else ""  # noqa: E731
    action = ((result or {}).get("disposition") or {}).get("action") or ""
    out: list = [P(f"<font color='#6b7a90'>FlowTrace · Case {x(case_no(o['case_id']))} ({x(o['case_id'])}) · "
                   f"{x(DECISION.get(action, action))}</font>", "meta"), Spacer(1, 2)]

    # Holds: firmest first.
    order = {"APPROVED": 0, "PROVISIONAL": 1, "PROPOSED": 2}
    holds = sorted((a for a in actions if a.get("type") == "HOLD"),
                   key=lambda a: (order.get(a.get("status"), 3), -(a.get("hold_amount") or 0)))
    out += [P("Holds", "h2")]
    if holds:
        rows = [[P(h, "th") for h in ("Account", "Amount frozen", "Status", "Until", "Decided by", "Reason")]]
        for a in holds[:CAP_HOLDS]:
            who = a.get("decided_by") or (a.get("held_by") if a.get("status") == "PROVISIONAL" else None)
            reason = a.get("reason") or a.get("hold_reason") or a.get("why") or ""
            rows.append([P(x(acct(a.get("account"))), "cell_b"), P(x(money(a.get("hold_amount"))), "cell"),
                         P(x(_hold_status(a)), "cell"), P(x(_hold_until(a)), "cell"),
                         P(x(_who(who) if who else "Awaiting a person"), "cell"), P(x(cut(reason, 120)), "cell")])
        out += [grid(rows, [82, 62, 74, 82, 62, W - 362])]
        if len(holds) > CAP_HOLDS:
            out += [more(len(holds) - CAP_HOLDS, "holds on the case page")]
        out += [P("A hold freezes the amount shown, not the whole account. The agent only proposes; a person approves "
                  "or rejects each hold with a reason.", "small")]
    else:
        out += [P("No hold was proposed for this case.")]

    # The key payments: the largest of the payments the case page lists.
    pays = sorted(o.get("transactions") or [], key=lambda p: (-p["amount"], p["ts"]))
    out += [P("Key payments", "h2")]
    if pays:
        rows = [[P(h, "th") for h in ("Time (UTC)", "From", "To", "Amount (USD eq.)", "Paid in", "Type", "Status")]]
        for p in pays[:CAP_PAYMENTS]:
            rows.append([P(x(utc(p["ts"])), "cell"), P(x(acct(p["src"])), "cell"), P(x(acct(p["dst"])), "cell"),
                         P(f"<b>{x(money(p['amount']))}</b>", "cell"), P(x(original(p.get("amount_orig"), p.get("cur"))), "cell"),
                         P(x(p.get("fmt") or ""), "cell"), P(x(STATUS.get(p.get("status"), "")), "cell")])
        out += [grid(rows, [74, 86, 86, 76, 72, 60, W - 454])]
        if len(pays) > CAP_PAYMENTS:
            out += [more(len(pays) - CAP_PAYMENTS, "payments on the case page")]
    else:
        out += [P("No payments.")]

    out += [P("STR draft narrative", "h2")] + _narrative(o, result, nm, acct)

    # Audit trail: the latest entries, oldest first.
    by_id = {a.get("id"): a for a in actions}
    out += [P("Audit trail", "h2")]
    if audit:
        recent = audit[:CAP_AUDIT][::-1]
        rows = [[P(h, "th") for h in ("Time (UTC)", "Who", "What", "Reason")]]
        for e in recent:
            kind = (by_id.get(e.get("action_id")) or {}).get("type")
            what = (f"Case verdict: {DECISION.get(e.get('to_status'), e.get('to_status') or '')}" if e.get("event") == "VERDICT"
                    else " ".join(w for w in (EVENT.get(e.get("event"), (e.get("event") or "").capitalize()),
                                              ACTION_TITLE.get(kind, "action"),
                                              f"on {acct(e['account'])}" if e.get("account") else "") if w))
            rows.append([P(x(utc(e.get("ts"))), "cell"), P(x(_who(e.get("actor"))), "cell"), P(x(what), "cell"),
                         P(x(cut(e.get("reason") or "", 110)), "cell")])
        out += [grid(rows, [74, 70, 160, W - 304])]
        if len(audit) > CAP_AUDIT:
            out += [more(len(audit) - CAP_AUDIT, "earlier entries in the audit log")]
    else:
        out += [P("No action has been recorded for this case yet.")]
    return out


def _refs(ids) -> str:
    ids = list(dict.fromkeys(i for i in ids if i))
    return f" <font color='#46566e'>[{x(', '.join(ids[:4]))}]</font>" if ids else ""


def _narrative(o: dict, result: dict, nm, acct) -> list:
    """Who / what / when / where / why / how, each from the case's own data, with the evidence it rests on (the IDs
    of the findings on page 1). The AI's narrative, when it wrote one (escalated cases), follows."""
    rep = (result or {}).get("report") or {}
    k = o.get("kpis") or {}
    nodes = (o.get("flow") or {}).get("nodes") or []
    t0, t1 = (o.get("window") or [None, None])[:2]

    who = []
    for role, label in (("Sender", "Sender"), ("Intermediary", "Intermediaries"), ("Destination", "Destination")):
        group = [n for n in nodes if n["role"] == role]
        if group:
            shown = ", ".join(acct(n["id"]) + (f" ({n['entity_type'].lower()})" if n.get("entity_type") else "")
                              for n in group[:3])
            who.append(f"{label}: {shown}" + (f" +{len(group) - 3} more" if len(group) > 3 else ""))
    pays = o.get("transactions") or []
    banks = {a.split(":")[0] for p in pays for a in (p["src"], p["dst"]) if ":" in a}
    fmts: dict[str, int] = {}
    for p in pays:
        if p.get("fmt"):
            fmts[p["fmt"]] = fmts.get(p["fmt"], 0) + 1
    curs = list(dict.fromkeys(p.get("cur") for p in pays if p.get("cur")))
    typ = o.get("typology") or ""
    findings = sorted(o.get("findings") or [], key=lambda f: -f.get("weight", 0))
    primary = o.get("primary")
    why = list(dict.fromkeys(f.get("brief", "") for f in findings if f.get("brief")))[:3]
    normal = sorted(o.get("explanations") or [], key=lambda f: -f.get("weight", 0))[:2]
    trail_ids = [i for t in rep.get("money_trail") or [] for i in t.get("evidence_ids", [])]
    rows = [
        ("Who", "; ".join(who) + "." if who else "-", (rep.get("subject_profile") or {}).get("evidence_ids", [])),
        ("What", f"{money(k.get('amount'))} in {plural(k.get('payments', 0), 'flagged payment')} between "
                 f"{plural(k.get('accounts', 0), 'account')}"
                 + (f", in a {TRICK[typ].lower()} pattern" if typ in TRICK else "") + ".", []),
        ("When", f"{utc(t0)} to {utc((t1 or 1) - 1)} UTC ({span(((t1 or 1) - 1) - (t0 or 0))})." if t0 is not None else "-", []),
        ("Where", (f"Across {plural(len(banks), 'bank')}" if banks else "Between the accounts above")
                  + (", paid by " + ", ".join(f"{f} ({n})" for f, n in sorted(fmts.items(), key=lambda kv: -kv[1])) if fmts else "")
                  + (f"; in {', '.join(curs[:4])}" if curs else "") + ".", []),
        ("Why", " ".join(b.rstrip(".…") + "." for b in why) if why else
                "No sign of laundering was found. " + " ".join(f.get("brief", "").rstrip(".…") + "." for f in normal),
         [f.get("id") for f in findings if f.get("brief") in why] or [f.get("id") for f in normal]),
        ("How", (PATTERN.get(typ, "Funds moved between the accounts above.").replace("{A}", nm(primary) or "the main account")
                 + (f" The investigation followed the money to {plural(k.get('traced_accounts', 0), 'further account')}."
                    if k.get("traced_accounts") else "")), trail_ids),
    ]
    t = Table([[P(f"<b>{w}</b>", "cell"), P(x(text) + _refs(ids), "cell")] for w, text, ids in rows], colWidths=[44, W - 44])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER),
                           ("BACKGROUND", (0, 0), (0, -1), SURFACE), ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]))
    out: list = [t]
    if rep.get("str_narrative"):
        out += [Spacer(1, 4), P("<b>Narrative drafted by AI, for a person to check before filing:</b> "
                                + x(cut(rep["str_narrative"], CAP_NARRATIVE)), "cell")]
    notes = list(dict.fromkeys(n for n in (o.get("narrative_note"), (o.get("summary") or {}).get("totals_note")) if n))
    out += [P(f"<b>{x(n)}</b>", "cell") for n in notes]
    out += [P("Evidence IDs in brackets (E1, E2…) are the findings on page 1. The Markdown draft has the full evidence list.", "small")]
    return out


# --- document ----------------------------------------------------------------------------------

class _Numbered(rl_canvas.Canvas):
    """Draws "Page N of M" once the page count is known."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._pages: list[dict] = []

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        for state in self._pages:
            self.__dict__.update(state)
            self.setFont("Helvetica", 7.5)
            self.setFillColor(INK2)
            self.drawRightString(PAGE_W - MARGIN, 18, f"Page {self._pageNumber} of {len(self._pages)}")
            super().showPage()
        super().save()


def _footer(c, _doc) -> None:
    c.saveState()
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.6)
    c.line(MARGIN, 34, PAGE_W - MARGIN, 34)
    c.setFont("Helvetica-Bold", 7.5)
    c.setFillColor(INK)
    c.drawString(MARGIN, 24, DISCLAIMER)
    c.setFont("Helvetica", 7)
    c.setFillColor(INK2)
    c.drawString(MARGIN, 14, CREDIT)
    c.restoreState()


def build_pdf(overview: dict, result: dict, actions: list[dict], audit: list[dict], now_ms: int | None = None) -> bytes:
    """The two-page report. overview is case_view.build_overview's output for the case; result its investigation;
    actions and audit its actions and audit entries (newest first), read without writing."""
    now_ms = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    buf = BytesIO()
    no = case_no(overview["case_id"])
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=FOOT,
                          title=f"FlowTrace case report: Case {no}", author="FlowTrace",
                          subject=f"AML case report for {overview['case_id']}", creator="FlowTrace")
    frame = Frame(MARGIN, FOOT, W, PAGE_H - MARGIN - FOOT, id="body", leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=_footer)])
    fit = lambda flow: KeepInFrame(W, frame._aH - 2, flow, mode="shrink")  # noqa: E731
    doc.build([fit(page_one(overview, result, actions, now_ms)), PageBreak(),
               fit(page_two(overview, result, actions, audit))], canvasmaker=_Numbered)
    return buf.getvalue()
