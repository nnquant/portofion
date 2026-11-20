"""assign colors to existing tags

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-20 10:00:00
"""
from collections import Counter, defaultdict

import sqlalchemy as sa
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

# frozen copy of domain.common.TAG_COLORS at the time of this migration
PALETTE = ("blue", "orange", "green", "violet", "amber", "cyan", "pink", "lime", "red", "indigo", "teal", "yellow")


def upgrade() -> None:
    bind = op.get_bind()
    tags = bind.execute(sa.text("SELECT id, workspace_id, color FROM tags ORDER BY workspace_id, id")).all()
    if not tags:
        return
    neighbours: dict[int, set[int]] = defaultdict(set)
    for a, b in bind.execute(sa.text(
        "SELECT a.tag_id, b.tag_id FROM strategy_tags a "
        "JOIN strategy_tags b ON a.strategy_id = b.strategy_id AND a.tag_id <> b.tag_id"
    )):
        neighbours[a].add(b)
    color = {t.id: t.color for t in tags}
    used: dict[int, Counter] = defaultdict(Counter)
    for t in tags:
        if t.color:
            used[t.workspace_id][t.color] += 1
    for t in tags:
        if t.color:
            continue
        avoid = {color[n] for n in neighbours[t.id] if color.get(n)}
        pool = [c for c in PALETTE if c not in avoid] or list(PALETTE)
        c = min(pool, key=lambda x: (used[t.workspace_id][x], PALETTE.index(x)))
        color[t.id] = c
        used[t.workspace_id][c] += 1
        bind.execute(sa.text("UPDATE tags SET color = :c WHERE id = :i"), {"c": c, "i": t.id})


def downgrade() -> None:
    op.execute("UPDATE tags SET color = NULL")
