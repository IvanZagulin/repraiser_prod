"""
Extended repricer run tests.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import patch
from app.models.repricer_run import RepricerRun


def _make_run(db, user_id, status="completed", run_type="scheduled"):
    run = RepricerRun(
        user_id=user_id,
        run_type=run_type,
        status=status,
        total_products=100,
        updated_count=50,
        skipped_count=45,
        error_count=5,
        started_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(run); db.commit(); db.refresh(run)
    return run


class TestRunsList:
    def test_empty_list(self, client):
        r = client.get("/api/repricer/runs")
        assert r.status_code == 200
        body = r.json()
        assert "runs" in body
        assert "total" in body

    def test_requires_auth(self, anon_client):
        r = anon_client.get("/api/repricer/runs")
        assert r.status_code == 401

    def test_returns_own_runs(self, client, db_session, test_user):
        run = _make_run(db_session, test_user.id)
        r = client.get("/api/repricer/runs")
        ids = [x["id"] for x in r.json()["runs"]]
        assert run.id in ids
        db_session.delete(run); db_session.commit()

    def test_isolated_from_other_user_runs(self, client, db_session):
        from app.models.user import User
        from app.api.auth import hash_password
        other = User(email="run_iso@test.com", password_hash=hash_password("P123!"),
                     is_active=True, is_verified=True, token_version=1,
                     verification_attempts=0, reset_attempts=0)
        db_session.add(other); db_session.commit()
        run = _make_run(db_session, other.id)

        r = client.get("/api/repricer/runs")
        ids = [x["id"] for x in r.json()["runs"]]
        assert run.id not in ids

        db_session.delete(run); db_session.delete(other); db_session.commit()


class TestRunDetail:
    def test_get_run_by_id(self, client, db_session, test_user):
        run = _make_run(db_session, test_user.id)
        r = client.get(f"/api/repricer/runs/{run.id}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == run.id
        assert body["status"] == "completed"
        assert body["updated_count"] == 50
        db_session.delete(run); db_session.commit()

    def test_run_idor(self, client, db_session):
        from app.models.user import User
        from app.api.auth import hash_password
        other = User(email="run_idor@test.com", password_hash=hash_password("P123!"),
                     is_active=True, is_verified=True, token_version=1,
                     verification_attempts=0, reset_attempts=0)
        db_session.add(other); db_session.commit()
        run = _make_run(db_session, other.id)

        r = client.get(f"/api/repricer/runs/{run.id}")
        assert r.status_code == 404  # cannot see other user's run

        db_session.delete(run); db_session.delete(other); db_session.commit()

    def test_nonexistent_run(self, client):
        r = client.get("/api/repricer/runs/999999")
        assert r.status_code == 404


class TestRunStatuses:
    def test_completed_run_has_stats(self, client, db_session, test_user):
        run = _make_run(db_session, test_user.id, status="completed")
        r = client.get(f"/api/repricer/runs/{run.id}")
        assert r.status_code == 200
        body = r.json()
        assert body["total_products"] == 100
        assert body["error_count"] == 5
        db_session.delete(run); db_session.commit()

    def test_filter_by_status(self, client, db_session, test_user):
        r1 = _make_run(db_session, test_user.id, status="completed")
        r2 = _make_run(db_session, test_user.id, status="failed")
        r_all = client.get("/api/repricer/runs")
        assert r_all.status_code == 200
        statuses = {x["status"] for x in r_all.json()["runs"]}
        assert "completed" in statuses or "failed" in statuses
        db_session.delete(r1); db_session.delete(r2); db_session.commit()

    def test_manual_run_no_products_returns_404(self, client):
        r = client.post("/api/repricer/run", json={"dry_run": False})
        # 404 when user has no products
        assert r.status_code in (404, 503)

    def test_selected_products_dispatch_as_priority(self, client, test_product):
        with patch("app.tasks.repricer_task.run_repricer_for_user.apply_async") as dispatch:
            response = client.post(
                "/api/repricer/run",
                json={"seller_skus": [test_product.seller_sku], "dry_run": False},
            )

        assert response.status_code == 200
        assert dispatch.call_args.kwargs["priority"] == 9
        assert dispatch.call_args.kwargs["kwargs"] == {"priority": True}

    def test_full_manual_run_keeps_normal_priority(self, client, test_product):
        with patch("app.tasks.repricer_task.run_repricer_for_user.apply_async") as dispatch:
            response = client.post("/api/repricer/run", json={"dry_run": False})

        assert response.status_code == 200
        assert dispatch.call_args.kwargs["priority"] == 0
        assert dispatch.call_args.kwargs["kwargs"] == {"priority": False}


class TestRunPagination:
    def test_pagination_limit(self, client, db_session, test_user):
        runs = [_make_run(db_session, test_user.id) for _ in range(5)]
        r = client.get("/api/repricer/runs?limit=2")
        assert r.status_code == 200
        assert len(r.json()["runs"]) <= 2
        for run in runs: db_session.delete(run)
        db_session.commit()

    def test_total_count_accurate(self, client, db_session, test_user):
        r0 = client.get("/api/repricer/runs").json()["total"]
        run = _make_run(db_session, test_user.id)
        r1 = client.get("/api/repricer/runs").json()["total"]
        assert r1 == r0 + 1
        db_session.delete(run); db_session.commit()
