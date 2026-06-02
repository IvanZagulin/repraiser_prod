"""
Tests for product groups API ??? CRUD and user isolation.
"""
import pytest
from app.models.product_group import ProductGroup


class TestGroupsList:
    def test_empty_list(self, client):
        r = client.get("/api/groups")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_requires_auth(self, anon_client):
        r = anon_client.get("/api/groups")
        assert r.status_code == 401

    def test_groups_isolated_by_user(self, client, db_session, test_user):
        from app.models.user import User
        from app.api.auth import hash_password
        other = User(email="other_g@test.com", password_hash=hash_password("Pass123!"),
                     is_active=True, is_verified=True, token_version=1)
        db_session.add(other); db_session.commit()
        g = ProductGroup(name="Other Group", user_id=other.id, color="#ff0000")
        db_session.add(g); db_session.commit()

        r = client.get("/api/groups")
        names = [x["name"] for x in r.json()]
        assert "Other Group" not in names

        db_session.delete(g); db_session.delete(other); db_session.commit()


class TestGroupsCreate:
    def test_create_valid(self, client):
        r = client.post("/api/groups", json={"name": "VIP", "color": "#7c3aed"})
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "VIP"
        assert "id" in body

    def test_create_default_color(self, client):
        r = client.post("/api/groups", json={"name": "Default Color Group"})
        assert r.status_code == 201
        assert r.json()["color"].startswith("#")

    def test_create_invalid_color(self, client):
        r = client.post("/api/groups", json={"name": "Bad", "color": "red"})
        assert r.status_code == 422

    def test_create_empty_name(self, client):
        r = client.post("/api/groups", json={"name": "", "color": "#000000"})
        assert r.status_code == 422

    def test_create_requires_auth(self, anon_client):
        r = anon_client.post("/api/groups", json={"name": "X", "color": "#000000"})
        assert r.status_code == 401


class TestGroupsUpdate:
    def test_update_name(self, client):
        r = client.post("/api/groups", json={"name": "Before", "color": "#aaaaaa"})
        gid = r.json()["id"]
        r2 = client.put(f"/api/groups/{gid}", json={"name": "After"})
        assert r2.status_code == 200
        assert r2.json()["name"] == "After"

    def test_cannot_update_other_user_group(self, client, db_session):
        from app.models.user import User
        from app.api.auth import hash_password
        other = User(email="other_upd@test.com", password_hash=hash_password("Pass123!"),
                     is_active=True, is_verified=True, token_version=1)
        db_session.add(other); db_session.commit()
        g = ProductGroup(name="Alien", user_id=other.id, color="#000000")
        db_session.add(g); db_session.commit()
        r = client.put(f"/api/groups/{g.id}", json={"name": "Hacked"})
        assert r.status_code == 404
        db_session.delete(g); db_session.delete(other); db_session.commit()


class TestGroupsDelete:
    def test_delete_own_group(self, client):
        r = client.post("/api/groups", json={"name": "To Delete", "color": "#000000"})
        gid = r.json()["id"]
        r2 = client.delete(f"/api/groups/{gid}")
        assert r2.status_code == 204

    def test_delete_nonexistent(self, client):
        r = client.delete("/api/groups/999999")
        assert r.status_code == 404
