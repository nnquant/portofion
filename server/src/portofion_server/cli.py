from __future__ import annotations

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)
user_app = typer.Typer(no_args_is_help=True)
app.add_typer(user_app, name="user")


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000, workers: int = 1, reload: bool = False):
    """Run the API server (and the built web UI)."""
    import uvicorn

    uvicorn.run("portofion_server.main:app", host=host, port=port, workers=workers, reload=reload,
                proxy_headers=True)


@app.command()
def migrate(revision: str = "head"):
    """Apply database migrations."""
    from portofion_server.db.migrate import upgrade

    upgrade(revision=revision)
    typer.echo("migrated")


@user_app.command("create")
def user_create(email: str, password: str = typer.Option(..., prompt=True, hide_input=True),
                name: str = "", superuser: bool = False):
    """Create a user (with a personal workspace)."""
    from portofion_server.db import session_factory
    from portofion_server.domain.accounts import create_user

    db = session_factory()()
    try:
        u = create_user(db, email, password, name, is_superuser=superuser)
        db.commit()
        typer.echo(f"created user {u.email} (id={u.id})")
    finally:
        db.close()


@app.command()
def seed(
    workspace: str = typer.Option("demo", help="workspace slug to fill (created if missing)"),
    strategies: int = 120,
    owner: str = typer.Option(None, help="email of a user to add as admin"),
    seed: int = 7,
    portfolios: bool = True,
):
    """Fill a workspace with synthetic strategies, extras, revisions and portfolios."""
    from portofion_server.seed import run_seed

    run_seed(workspace, strategies, owner, seed, portfolios)


@app.command()
def recompute(workspace: str = typer.Option(None), all_versions: bool = False):
    """Recompute cached version metrics (stale only, unless --all-versions)."""
    from sqlalchemy import select

    from portofion_server.db import session_factory
    from portofion_server.db.models import Workspace
    from portofion_server.domain.metrics_cache import recompute_workspace

    db = session_factory()()
    try:
        q = select(Workspace)
        if workspace:
            q = q.where(Workspace.slug == workspace)
        for ws in db.scalars(q).all():
            n = recompute_workspace(db, ws, only_stale=not all_versions)
            db.commit()
            typer.echo(f"{ws.slug}: {n} versions recomputed")
    finally:
        db.close()


if __name__ == "__main__":
    app()
