from datetime import UTC, datetime, timedelta

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from httpx import Response

from stapi_fastapi.models.opportunity import OpportunityRequest

from .backends import MockProductBackend
from .utils import find_link

NOW = datetime.now(UTC)
START = NOW
END = START + timedelta(days=5)


@pytest.fixture
def new_order_response(
    product_id: str,
    product_backend: MockProductBackend,
    stapi_client: TestClient,
    allowed_payloads: list[OpportunityRequest],
) -> Response:
    product_backend._allowed_payloads = allowed_payloads

    res = stapi_client.post(
        f"products/{product_id}/order",
        json=allowed_payloads[0].model_dump(),
    )

    assert res.status_code == status.HTTP_201_CREATED
    assert res.headers["Content-Type"] == "application/geo+json"
    return res


@pytest.mark.parametrize("product_id", ["test-spotlight"])
def test_new_order_location_header_matches_self_link(
    new_order_response: Response,
) -> None:
    order = new_order_response.json()
    link = find_link(order["links"], "self")
    assert link
    assert new_order_response.headers["Location"] == str(link["href"])


@pytest.fixture
def get_order_response(
    stapi_client: TestClient, new_order_response: Response
) -> Response:
    order_id = new_order_response.json()["id"]

    res = stapi_client.get(f"/orders/{order_id}")
    assert res.status_code == status.HTTP_200_OK
    assert res.headers["Content-Type"] == "application/geo+json"
    return res


@pytest.mark.parametrize("product_id", ["test-spotlight"])
def test_get_order_properties(get_order_response: Response, allowed_payloads) -> None:
    order = get_order_response.json()

    assert order["geometry"] == {
        "type": "Point",
        "coordinates": list(allowed_payloads[0].geometry.coordinates),
    }

    assert (
        order["properties"]["datetime"] == allowed_payloads[0].model_dump()["datetime"]
    )
