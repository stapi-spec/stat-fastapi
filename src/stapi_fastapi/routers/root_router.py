import logging
import traceback
from typing import Self

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.datastructures import URL
from returns.maybe import Maybe, Some
from returns.result import Failure, Success

from stapi_fastapi.backends.root_backend import (
    GetOpportunitySearchRecord,
    GetOpportunitySearchRecords,
    GetOrder,
    GetOrders,
    GetOrderStatuses,
)
from stapi_fastapi.constants import TYPE_GEOJSON, TYPE_JSON
from stapi_fastapi.exceptions import NotFoundException
from stapi_fastapi.models.conformance import (
    ASYNC_OPPORTUNITIES,
    CORE,
    Conformance,
)
from stapi_fastapi.models.opportunity import (
    OpportunitySearchRecord,
    OpportunitySearchRecords,
)
from stapi_fastapi.models.order import (
    Order,
    OrderCollection,
    OrderStatuses,
)
from stapi_fastapi.models.product import Product, ProductsCollection
from stapi_fastapi.models.root import RootResponse
from stapi_fastapi.models.shared import Link
from stapi_fastapi.responses import GeoJSONResponse
from stapi_fastapi.routers.product_router import ProductRouter

logger = logging.getLogger(__name__)


class RootRouter(APIRouter):
    def __init__(
        self: Self,
        get_orders: GetOrders,
        get_order: GetOrder,
        get_order_statuses: GetOrderStatuses,
        get_opportunity_search_records: GetOpportunitySearchRecords | None = None,
        get_opportunity_search_record: GetOpportunitySearchRecord | None = None,
        conformances: list[str] = [CORE],
        name: str = "root",
        openapi_endpoint_name: str = "openapi",
        docs_endpoint_name: str = "swagger_ui_html",
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        if ASYNC_OPPORTUNITIES in conformances and (
            not get_opportunity_search_records or not get_opportunity_search_record
        ):
            raise ValueError(
                "`get_opportunity_search_records` and `get_opportunity_search_record` "
                "are required when advertising async opportunity search conformance"
            )

        self._get_orders = get_orders
        self._get_order = get_order
        self._get_order_statuses = get_order_statuses
        if get_opportunity_search_records is not None:
            self._get_opportunity_search_records = get_opportunity_search_records
        if get_opportunity_search_record is not None:
            self._get_opportunity_search_record = get_opportunity_search_record
        self.conformances = conformances
        self.name = name
        self.openapi_endpoint_name = openapi_endpoint_name
        self.docs_endpoint_name = docs_endpoint_name
        self.product_ids: list[str] = []

        # A dict is used to track the product routers so we can ensure
        # idempotentcy in case a product is added multiple times, and also to
        # manage clobbering if multiple products with the same product_id are
        # added.
        self.product_routers: dict[str, ProductRouter] = {}

        self.add_api_route(
            "/",
            self.get_root,
            methods=["GET"],
            name=f"{self.name}:root",
            tags=["Root"],
        )

        self.add_api_route(
            "/conformance",
            self.get_conformance,
            methods=["GET"],
            name=f"{self.name}:conformance",
            tags=["Conformance"],
        )

        self.add_api_route(
            "/products",
            self.get_products,
            methods=["GET"],
            name=f"{self.name}:list-products",
            tags=["Products"],
        )

        self.add_api_route(
            "/orders",
            self.get_orders,
            methods=["GET"],
            name=f"{self.name}:list-orders",
            response_class=GeoJSONResponse,
            tags=["Orders"],
        )

        self.add_api_route(
            "/orders/{order_id}",
            self.get_order,
            methods=["GET"],
            name=f"{self.name}:get-order",
            response_class=GeoJSONResponse,
            tags=["Orders"],
        )

        self.add_api_route(
            "/orders/{order_id}/statuses",
            self.get_order_statuses,
            methods=["GET"],
            name=f"{self.name}:list-order-statuses",
            tags=["Orders"],
        )

        if ASYNC_OPPORTUNITIES in conformances:
            self.add_api_route(
                "/searches/opportunities",
                self.get_opportunity_search_records,
                methods=["GET"],
                name=f"{self.name}:list-opportunity-search-records",
                summary="List all Opportunity Search Records",
                tags=["Opportunities"],
            )

            self.add_api_route(
                "/searches/opportunities/{search_record_id}",
                self.get_opportunity_search_record,
                methods=["GET"],
                name=f"{self.name}:get-opportunity-search-record",
                summary="Get an Opportunity Search Record by ID",
                tags=["Opportunities"],
            )

    def get_root(self: Self, request: Request) -> RootResponse:
        links = [
            Link(
                href=str(request.url_for(f"{self.name}:root")),
                rel="self",
                type=TYPE_JSON,
            ),
            Link(
                href=str(request.url_for(f"{self.name}:conformance")),
                rel="conformance",
                type=TYPE_JSON,
            ),
            Link(
                href=str(request.url_for(f"{self.name}:list-products")),
                rel="products",
                type=TYPE_JSON,
            ),
            Link(
                href=str(request.url_for(f"{self.name}:list-orders")),
                rel="orders",
                type=TYPE_JSON,
            ),
            Link(
                href=str(request.url_for(self.openapi_endpoint_name)),
                rel="service-description",
                type=TYPE_JSON,
            ),
            Link(
                href=str(request.url_for(self.docs_endpoint_name)),
                rel="service-docs",
                type="text/html",
            ),
        ]

        if self.supports_async_opportunity_search:
            links.insert(
                -2,
                Link(
                    href=str(
                        request.url_for(f"{self.name}:list-opportunity-search-records")
                    ),
                    rel="opportunity-search-records",
                    type=TYPE_JSON,
                ),
            )

        return RootResponse(
            id="STAPI API",
            conformsTo=self.conformances,
            links=links,
        )

    def get_conformance(self: Self) -> Conformance:
        return Conformance(conforms_to=self.conformances)

    def get_products(
        self: Self, request: Request, next: str | None = None, limit: int = 10
    ) -> ProductsCollection:
        start = 0
        limit = min(limit, 100)
        try:
            if next:
                start = self.product_ids.index(next)
        except ValueError:
            logging.exception("An error occurred while retrieving products")
            raise NotFoundException(
                detail="Error finding pagination token for products"
            ) from None
        end = start + limit
        ids = self.product_ids[start:end]
        links = [
            Link(
                href=str(request.url_for(f"{self.name}:list-products")),
                rel="self",
                type=TYPE_JSON,
            ),
        ]
        if end > 0 and end < len(self.product_ids):
            links.append(self.pagination_link(request, self.product_ids[end]))
        return ProductsCollection(
            products=[
                self.product_routers[product_id].get_product(request)
                for product_id in ids
            ],
            links=links,
        )

    async def get_orders(
        self: Self, request: Request, next: str | None = None, limit: int = 10
    ) -> OrderCollection:
        links: list[Link] = []
        match await self._get_orders(next, limit, request):
            case Success((orders, Some(pagination_token))):
                for order in orders:
                    order.links.append(self.order_link(request, order))
                links.append(self.pagination_link(request, pagination_token))
            case Success((orders, Nothing)):  # noqa: F841
                for order in orders:
                    order.links.append(self.order_link(request, order))
            case Failure(ValueError()):
                raise NotFoundException(detail="Error finding pagination token")
            case Failure(e):
                logger.error(
                    "An error occurred while retrieving orders: %s",
                    traceback.format_exception(e),
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Error finding Orders",
                )
            case _:
                raise AssertionError("Expected code to be unreachable")
        return OrderCollection(features=orders, links=links)

    async def get_order(self: Self, order_id: str, request: Request) -> Order:
        """
        Get details for order with `order_id`.
        """
        match await self._get_order(order_id, request):
            case Success(Some(order)):
                self.add_order_links(order, request)
                return order
            case Success(Maybe.empty):
                raise NotFoundException("Order not found")
            case Failure(e):
                logger.error(
                    "An error occurred while retrieving order '%s': %s",
                    order_id,
                    traceback.format_exception(e),
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Error finding Order",
                )
            case _:
                raise AssertionError("Expected code to be unreachable")

    async def get_order_statuses(
        self: Self,
        order_id: str,
        request: Request,
        next: str | None = None,
        limit: int = 10,
    ) -> OrderStatuses:
        links: list[Link] = []
        match await self._get_order_statuses(order_id, next, limit, request):
            case Success((statuses, Some(pagination_token))):
                links.append(self.order_statuses_link(request, order_id))
                links.append(self.pagination_link(request, pagination_token))
            case Success((statuses, Nothing)):  # noqa: F841
                links.append(self.order_statuses_link(request, order_id))
            case Failure(KeyError()):
                raise NotFoundException("Error finding pagination token")
            case Failure(e):
                logger.error(
                    "An error occurred while retrieving order statuses: %s",
                    traceback.format_exception(e),
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Error finding Order Statuses",
                )
            case _:
                raise AssertionError("Expected code to be unreachable")
        return OrderStatuses(statuses=statuses, links=links)

    def add_product(self: Self, product: Product, *args, **kwargs) -> None:
        # Give the include a prefix from the product router
        product_router = ProductRouter(product, self, *args, **kwargs)
        self.include_router(product_router, prefix=f"/products/{product.id}")
        self.product_routers[product.id] = product_router
        self.product_ids = [*self.product_routers.keys()]

    def generate_order_href(self: Self, request: Request, order_id: str) -> URL:
        return request.url_for(f"{self.name}:get-order", order_id=order_id)

    def generate_order_statuses_href(
        self: Self, request: Request, order_id: str
    ) -> URL:
        return request.url_for(f"{self.name}:list-order-statuses", order_id=order_id)

    def add_order_links(self: Self, order: Order, request: Request):
        order.links.append(
            Link(
                href=str(self.generate_order_href(request, order.id)),
                rel="self",
                type=TYPE_GEOJSON,
            )
        )
        order.links.append(
            Link(
                href=str(self.generate_order_statuses_href(request, order.id)),
                rel="monitor",
                type=TYPE_JSON,
            ),
        )

    def order_link(self, request: Request, order: Order):
        return Link(
            href=str(request.url_for(f"{self.name}:get-order", order_id=order.id)),
            rel="self",
            type=TYPE_JSON,
        )

    def order_statuses_link(self, request: Request, order_id: str):
        return Link(
            href=str(
                request.url_for(
                    f"{self.name}:list-order-statuses",
                    order_id=order_id,
                )
            ),
            rel="self",
            type=TYPE_JSON,
        )

    def pagination_link(self, request: Request, pagination_token: str):
        return Link(
            href=str(request.url.include_query_params(next=pagination_token)),
            rel="next",
            type=TYPE_JSON,
        )

    async def get_opportunity_search_records(
        self: Self, request: Request, next: str | None = None, limit: int = 10
    ) -> OpportunitySearchRecords:
        links: list[Link] = []
        match await self._get_opportunity_search_records(next, limit, request):
            case Success((records, Some(pagination_token))):
                for record in records:
                    self.add_opportunity_search_record_self_link(record, request)
                links.append(self.pagination_link(request, pagination_token))
            case Success((records, Nothing)):  # noqa: F841
                for record in records:
                    self.add_opportunity_search_record_self_link(record, request)
            case Failure(ValueError()):
                raise NotFoundException(detail="Error finding pagination token")
            case Failure(e):
                logger.error(
                    "An error occurred while retrieving opportunity search records: %s",
                    traceback.format_exception(e),
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Error finding Opportunity Search Records",
                )
            case _:
                raise AssertionError("Expected code to be unreachable")
        return OpportunitySearchRecords(search_records=records, links=links)

    async def get_opportunity_search_record(
        self: Self, search_record_id: str, request: Request
    ) -> OpportunitySearchRecord:
        """
        Get the Opportunity Search Record with `search_record_id`.
        """
        match await self._get_opportunity_search_record(search_record_id, request):
            case Success(Some(search_record)):
                self.add_opportunity_search_record_self_link(search_record, request)
                return search_record
            case Success(Maybe.empty):
                raise NotFoundException("Opportunity Search Record not found")
            case Failure(e):
                logger.error(
                    "An error occurred while retrieving opportunity search record '%s': %s",
                    search_record_id,
                    traceback.format_exception(e),
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Error finding Opportunity Search Record",
                )
            case _:
                raise AssertionError("Expected code to be unreachable")

    def generate_opportunity_search_record_href(
        self: Self, request: Request, search_record_id: str
    ) -> URL:
        return request.url_for(
            f"{self.name}:get-opportunity-search-record",
            search_record_id=search_record_id,
        )

    def add_opportunity_search_record_self_link(
        self: Self, opportunity_search_record: OpportunitySearchRecord, request: Request
    ) -> None:
        opportunity_search_record.links.append(
            Link(
                href=str(
                    self.generate_opportunity_search_record_href(
                        request, opportunity_search_record.id
                    )
                ),
                rel="self",
                type=TYPE_JSON,
            )
        )

    @property
    def supports_async_opportunity_search(self: Self) -> bool:
        return (
            ASYNC_OPPORTUNITIES in self.conformances
            and hasattr(self, "_get_opportunity_search_records")
            and hasattr(self, "_get_opportunity_search_record")
        )
