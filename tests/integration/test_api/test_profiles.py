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


class TestProfileResponseFields:
    """ProfileResponse must include created_at and is_default fields."""

    def test_list_profiles_includes_created_at(self, api_client):
        """GET /profiles/ items include a non-null created_at timestamp."""
        profiles = api_client.get("/api/v1/profiles/").json()
        assert len(profiles) >= 1
        for p in profiles:
            assert "created_at" in p, f"created_at missing from profile {p}"
            assert p["created_at"] is not None

    def test_list_profiles_includes_is_default(self, api_client):
        """GET /profiles/ items include is_default; the auto-provisioned profile is default."""
        profiles = api_client.get("/api/v1/profiles/").json()
        assert len(profiles) >= 1
        # In local mode the auto-provisioned profile is the user's default.
        assert "is_default" in profiles[0], "is_default missing from profile"
        assert profiles[0]["is_default"] is True

    def test_create_profile_response_includes_created_at_and_is_default(
        self, api_client
    ):
        """POST /profiles/ response includes created_at and is_default."""
        resp = api_client.post("/api/v1/profiles/", json={"name": "FieldsCheck"})
        assert resp.status_code == 201
        body = resp.json()
        assert "created_at" in body
        assert "is_default" in body
        assert body["created_at"] is not None

    def test_set_default_profile_updates_is_default(self, api_client):
        """PATCH /{id} with default=true makes that profile is_default=True."""
        # Create a second profile.
        second = api_client.post(
            "/api/v1/profiles/", json={"name": "Second Default Test"}
        ).json()
        second_id = second["id"]

        # Set it as default.
        resp = api_client.patch(f"/api/v1/profiles/{second_id}", json={"default": True})
        assert resp.status_code == 200
        assert resp.json()["is_default"] is True

        # List profiles — the new default is_default=True, others are False.
        profiles = api_client.get("/api/v1/profiles/").json()
        defaults = [p for p in profiles if p["is_default"]]
        assert len(defaults) == 1, f"Expected exactly 1 default, got: {defaults}"
        assert defaults[0]["id"] == second_id
