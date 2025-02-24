import csv
import hashlib
import json
import os
import re
import time
from typing import Any, Callable, Generator
from urllib.parse import urlencode

import requests
from diskcache import Cache
from gql import Client, gql
from gql.transport.requests import RequestsHTTPTransport
from lxml import html

from juniorguru.lib import loggers


USER_AGENT = "JuniorGuruBot (+https://junior.guru)"

COLLECTION_NAME_RE = re.compile(r"(?P<collection_name>\w+)\(after:\s*\$cursor")

MEMBERFUL_API_KEY = os.environ.get("MEMBERFUL_API_KEY")

MEMBERFUL_EMAIL = os.environ.get("MEMBERFUL_EMAIL", "kure@junior.guru")

MEMBERFUL_PASSWORD = os.environ.get("MEMBERFUL_PASSWORD")

DOWNLOAD_POLLING_WAIT_SEC = 5


logger = loggers.from_path(__file__)


class MemberfulAPI:
    # https://memberful.com/help/integrate/advanced/memberful-api/
    # https://juniorguru.memberful.com/api/graphql/explorer?api_user_id=52463

    def __init__(
        self,
        api_key: str = None,
        cache: Cache = None,
        clear_cache: bool = False,
    ):
        self.cache = cache
        self.clear_cache = clear_cache
        self.api_key = api_key or MEMBERFUL_API_KEY
        self._client = None

    @property
    def client(self) -> Client:
        if not self._client:
            logger.debug("Connecting")
            transport = RequestsHTTPTransport(
                url="https://juniorguru.memberful.com/api/graphql/",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "User-Agent": USER_AGENT,
                },
                verify=True,
                retries=3,
            )
            self._client = Client(transport=transport)
        return self._client

    def mutate(self, mutation: str, variable_values: dict) -> dict[str, Any]:
        logger.debug("Sending a mutation")
        return self.client.execute(gql(mutation), variable_values=variable_values)

    def get_nodes(
        self, query: str, variable_values: dict = None
    ) -> Generator[dict, None, None]:
        if match := COLLECTION_NAME_RE.search(query):
            collection_name = match.group("collection_name")
        else:
            raise ValueError("Could not parse collection name")
        declared_count = None
        nodes_count = 0
        duplicates_count = 0
        seen_node_ids = set()
        for result in self._query(
            query,
            lambda result: result[collection_name]["pageInfo"],
            variable_values=variable_values,
        ):
            # save total count so we can later check if we got all the nodes
            count = result[collection_name]["totalCount"]
            if declared_count is None:
                logger.debug(f"Expecting {count} nodes")
                declared_count = count
            assert (
                declared_count == count
            ), f"Memberful API suddenly declares different total count: {count} (≠ {declared_count})"

            # iterate over nodes and drop duplicates, because, unfortunately, the API returns duplicates
            for edge in result[collection_name]["edges"]:
                node = edge["node"]
                node_id = node.get("id") or hash_data(node)
                if node_id in seen_node_ids:
                    logger.debug(f"Dropping a duplicate node: {node_id!r}")
                    duplicates_count += 1
                else:
                    yield node
                    seen_node_ids.add(node_id)
                nodes_count += 1
        assert (
            duplicates_count == 0
        ), f"Memberful API returned {duplicates_count} duplicate nodes"
        assert (
            declared_count == nodes_count
        ), f"Memberful API returned {nodes_count} nodes instead of {declared_count}"

    def _query(self, query: str, get_page_info: Callable, variable_values: dict = None):
        variable_values = variable_values or {}
        cursor = ""
        n = 0
        while cursor is not None:
            logger.debug(f"Sending a query with cursor {cursor!r}")
            result = self._execute_query(query, dict(cursor=cursor, **variable_values))
            yield result
            n += 1
            page_info = get_page_info(result)
            if page_info["hasNextPage"]:
                cursor = page_info["endCursor"]
            else:
                cursor = None

    def _execute_query(self, query: str, variable_values: dict) -> dict:
        cache_tag = self.__class__.__name__.lower()

        if self.cache:
            if self.clear_cache:
                logger.debug("Clearing cache")
                self.cache.evict(cache_tag)
                self.clear_cache = False

            cache_key = hash_data(dict(query=query, variable_values=variable_values))
            try:
                result = self.cache[cache_key]
                logger.debug(f"Loading from cache: {cache_key}")
                return result
            except KeyError:
                pass

        logger.debug(
            f"Querying Memberful API, variable values: {json.dumps(variable_values)}"
        )
        result = self.client.execute(gql(query), variable_values=variable_values)

        if self.cache:
            logger.debug(f"Saving to cache: {cache_key}")
            self.cache.set(cache_key, result, tag=cache_tag)

        return result


def hash_data(data: dict) -> str:
    return hashlib.sha256(json.dumps(data).encode()).hexdigest()


class DownloadError(Exception):
    pass


class MemberfulCSV:
    def __init__(
        self,
        email: str = None,
        password: str = None,
        cache: Cache = None,
        clear_cache: bool = False,
    ):
        self.cache = cache
        self.clear_cache = clear_cache
        self.email = email or MEMBERFUL_EMAIL
        self.password = password or MEMBERFUL_PASSWORD
        self._session = None
        self._csrf_token = None

    @property
    def session(self) -> requests.Session:
        if not self._session:
            self._session, self._csrf_token = self._auth()
        return self._session

    @property
    def csrf_token(self) -> str:
        if not self._csrf_token:
            self._session, self._csrf_token = self._auth()
        return self._csrf_token

    def _auth(self) -> tuple[requests.Session, Any]:
        logger.debug("Logging into Memberful")
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        response = session.get("https://juniorguru.memberful.com/admin/auth/sign_in")
        response.raise_for_status()
        html_tree = html.fromstring(response.content)
        html_tree.make_links_absolute(response.url)
        form = html_tree.forms[0]
        form.fields["email"] = self.email
        form.fields["password"] = self.password
        response = session.post(form.action, data=form.form_values())
        response.raise_for_status()
        html_tree = html.fromstring(response.content)
        csrf_token = html_tree.cssselect('meta[name="csrf-token"]')[0].get("content")
        logger.debug("Success!")
        return session, csrf_token

    def download_csv(self, params: dict):
        cache_tag = self.__class__.__name__.lower()

        url = f"https://juniorguru.memberful.com/admin/csv_exports?{urlencode(params)}"
        logger.debug(f"Looking CSV export: {url}")

        if self.cache:
            if self.clear_cache:
                logger.debug("Clearing cache")
                self.cache.evict(cache_tag)
                self.clear_cache = False

            cache_key = hash_data(dict(url=url))
            try:
                data = self.cache[cache_key]
                logger.debug(f"Loading from cache: {cache_key}")
                return self._parse_csv(data)
            except KeyError:
                pass

        logger.debug("Downloading from Memberful website")
        response = self.session.post(
            url, allow_redirects=False, headers={"X-CSRF-Token": self.csrf_token}
        )
        response.raise_for_status()
        download_url = f"{response.headers['Location']}/download"

        success = False
        for attempt_no in range(1, 10):
            logger.debug(
                f"Attempt #{attempt_no}, waiting {DOWNLOAD_POLLING_WAIT_SEC}s for {download_url}"
            )
            time.sleep(DOWNLOAD_POLLING_WAIT_SEC)
            try:
                response = self.session.get(download_url)
                response.raise_for_status()
            except requests.exceptions.HTTPError as e:
                logger.debug(str(e))
            else:
                logger.debug("Success!")
                success = True
                break
        if not success:
            raise DownloadError("Failed to download the CSV export")
        data = response.content.decode("utf-8")

        if self.cache:
            logger.debug(f"Saving to cache: {cache_key}")
            self.cache.set(cache_key, data, tag=cache_tag)

        return self._parse_csv(data)

    def _parse_csv(self, content: str) -> csv.DictReader:
        return csv.DictReader(content.splitlines())


def memberful_url(account_id: int | str) -> str:
    return f"https://juniorguru.memberful.com/admin/members/{account_id}/"
