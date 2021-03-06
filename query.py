#!/usr/bin/env python3

"""
This service will serve queries to the registrys. Queries are fetched from
one or more frontend servers having a web interface or similar.

The queries are saved and cached in the query server with info about who has
already signed it. This list is compared at registries to decide if the query
should be verified and processed.

Registry servers sends one type of request, and get a combined result back from
the query server, which will be a subset of this query cache, with the queries
relevant for this registry. The registry get a limited time to sign queries and
return. During that time, signing is blocked for other registries.
"""

import json
import logging
import argparse
import socketserver
from urllib import request

import config
from lib import sign, verify
from lib import serialize_encrypt_and_encode, decode_decrypt_and_deserialize

logging.getLogger("gnupg").setLevel(logging.INFO)
logger = logging.getLogger('query')

signed_query_store = {}


def fetch_queries(registry_id):
    """Fetching queries. Will also sign those not seen before."""

    data = request.urlopen(config.WEB_SERVER_QUERY_URL).read()
    logger.debug("all queries %s", data.decode("utf-8"))

    queries = json.loads(data.decode("utf-8"))
    for query in queries['queries']:
        # fill local cache
        query_id = query.get('id')
        if not query.get('status') and query_id not in signed_query_store:
            query['signed_by'] = []
            original = json.dumps(query['fields'])
            signed = sign(original)
            query['signed'] = signed.data.decode("utf-8")
            signed_query_store[query_id] = query
        elif query.get('status'):
            del signed_query_store[query_id]


def filter_queries(registry_id):
    filtered = []
    for query_id, query in signed_query_store.items():
        if registry_id in query['sources']:
            filtered.append(query)
    logger.debug("filtered    %s", filtered)
    return filtered


class QueryHandler(socketserver.StreamRequestHandler):

    def handle(self):
        self.data = self.rfile.readline().strip()
        data = json.loads(self.data.decode("utf-8"))
        source_id = data['source_id']

        logger.info("%s gets queries", source_id)

        # TODO: Create good filter
        # TODO: Source ID should be replaced by mapping to IP/PORT
        valid_sources = ['hunt', 'cancer', 'death']
        if 'source_id' in data and source_id in valid_sources:
            # fetching and saving to "global" list of all queries
            fetch_queries(source_id)
            queries = filter_queries(source_id)
            logger.info("got queries from web server")

            if not config.RECIPIENTS[source_id]:
                raise Exception("Could not find encryption config for %s",
                                source_id)

            encrypted = serialize_encrypt_and_encode(
                    {"queries": queries}, config.RECIPIENTS[source_id])

            logger.debug("sending     %s", encrypted)
            logger.info("responding to %s with all %s queries",
                        source_id, len(queries))
            self.request.sendall(encrypted + bytes("\n", "utf-8"))

            received = self.rfile.readline().strip()
            logger.debug("received  %s", received)

            signed_queries = decode_decrypt_and_deserialize(received)
            logger.info("got back %s signed queries from %s",
                        len(signed_queries), source_id)

            logger.debug("decrypted_data %s", signed_queries)
            # reset timeout lock
            for query_id, query in signed_queries.items():
                # verify
                verified = verify(query)
                if not verified:
                    raise ValueError(
                        "Signature could not be verified for query_id %s",
                        query_id)

                # update signed_queries
                signed_query_store[query_id]['signed_by'].append(source_id)
                signed_query_store[query_id]['signed'] = query

            logger.debug(signed_query_store)
            logger.info("%s got %s queries", source_id, len(signed_query_store))


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Query server")
    parser.add_argument('--debug', nargs='?', const=True, default=False,
                        help="Debug logging")
    args = parser.parse_args()

    level = logging.INFO
    if args.debug:
        level = logging.DEBUG

    logging.basicConfig(
        level=level,
        format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s'
    )
    server = socketserver.TCPServer((config.QUERY_SERVER_HOST,
                                     config.QUERY_SERVER_PORT),
                                    QueryHandler)
    server.serve_forever()
