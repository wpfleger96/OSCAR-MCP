"""Integration tests for the profiles API router.

These tests verify that:
- Routes are accessible without a ?request= query parameter (the old `request: object`
  annotation caused FastAPI to expose `request` as a required query parameter,
  returning 422 on every call).
- Routes behave correctly in local mode (auto-provisioned actor via ActorDep).
"""


class TestListProfiles:
    def test_list_returns_200_without_request_query_param(self, api_client):
        """GET /profiles/ must return 200, not 422 (annotation bug regression guard)."""
        response = api_client.get("/api/v1/profiles/")
        assert response.status_code == 200

    def test_list_returns_json_array(self, api_client):
        response = api_client.get("/api/v1/profiles/")
        assert isinstance(response.json(), list)

    def test_list_includes_auto_provisioned_profile(self, api_client):
        """Local mode auto-provisions a default profile; it must appear in the list."""
        response = api_client.get("/api/v1/profiles/")
        profiles = response.json()
        assert len(profiles) >= 1
        p = profiles[0]
        assert "id" in p
        assert "name" in p
        assert "user_id" in p

    def test_list_does_not_accept_request_as_query_param(self, api_client):
        """Passing request= as a query param must NOT cause a 422; it is ignored."""
        response = api_client.get("/api/v1/profiles/?request=anything")
        # The route should still work (200), not blow up with a query-param conflict.
        assert response.status_code == 200


class TestCreateProfile:
    def test_create_returns_201_without_request_query_param(self, api_client):
        """POST /profiles/ must return 201, not 422 (annotation bug regression guard)."""
        response = api_client.post("/api/v1/profiles/", json={"name": "My New Profile"})
        assert response.status_code == 201

    def test_create_returns_profile_fields(self, api_client):
        response = api_client.post("/api/v1/profiles/", json={"name": "Work Profile"})
        body = response.json()
        assert body["name"] == "Work Profile"
        assert "id" in body
        assert "user_id" in body

    def test_create_duplicate_name_returns_409(self, api_client):
        api_client.post("/api/v1/profiles/", json={"name": "Dup"})
        response = api_client.post("/api/v1/profiles/", json={"name": "Dup"})
        assert response.status_code == 409

    def test_create_appears_in_list(self, api_client):
        api_client.post("/api/v1/profiles/", json={"name": "Listed"})
        profiles = api_client.get("/api/v1/profiles/").json()
        names = [p["name"] for p in profiles]
        assert "Listed" in names


class TestUpdateProfile:
    def test_rename_returns_200_without_request_query_param(self, api_client):
        """PATCH /profiles/{id} must return 200, not 422 (annotation bug regression guard)."""
        # Ensure at least one profile exists (auto-provisioned in local mode).
        profiles = api_client.get("/api/v1/profiles/").json()
        profile_id = profiles[0]["id"]
        response = api_client.patch(
            f"/api/v1/profiles/{profile_id}", json={"name": "Renamed"}
        )
        assert response.status_code == 200

    def test_rename_updates_name(self, api_client):
        profiles = api_client.get("/api/v1/profiles/").json()
        profile_id = profiles[0]["id"]
        response = api_client.patch(
            f"/api/v1/profiles/{profile_id}", json={"name": "New Name"}
        )
        assert response.json()["name"] == "New Name"

    def test_patch_foreign_profile_returns_404(self, api_client):
        response = api_client.patch("/api/v1/profiles/999999", json={"name": "Ghost"})
        assert response.status_code == 404

    def test_patch_no_fields_returns_422(self, api_client):
        profiles = api_client.get("/api/v1/profiles/").json()
        profile_id = profiles[0]["id"]
        response = api_client.patch(f"/api/v1/profiles/{profile_id}", json={})
        assert response.status_code == 422
