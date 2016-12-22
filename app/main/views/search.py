# -*- coding: utf-8 -*-
from collections import defaultdict
import json
from flask.helpers import url_for
import os
from flask import abort, render_template, request, redirect, current_app
import flask_featureflags as feature
from app.api_client.data import DataAPIClient
from app.main.utils import get_page_list

from ...main import main
from app import cache


# For prototyping
from collections import namedtuple

Filter = namedtuple('Filter', 'name options')
Option = namedtuple('Option', 'name value label checked')
Badge = namedtuple('Badge', 'css_class label')
Role = namedtuple('Role', 'label')
ExtraDetail = namedtuple('ExtraDetail', 'key value')
Result = namedtuple('Result', 'title description badges roles url')

SUPPLIER_RESULTS_PER_PAGE = 50


def normalise_role(role_name):
    return role_name.replace('Senior ', '').replace('Junior ', '')  # Mind the white space after Junior


def get_all_roles(data_api_client):
    """
    Returns a two-valued tuple:
    1. A set containing all role strings
    2. A map from role strings to original role data

    The original role data is actually a list of dicts because of folding Senior/Junior roles into one role.
    """
    response = data_api_client.get_roles()

    roles = set()
    raw_role_data = defaultdict(list)
    for role_data in response['roles']:
        role = normalise_role(role_data['role'])
        raw_role_data[role].append(role_data)
        roles.add(role)
    return roles, raw_role_data


def get_all_domains(data_api_client):
    return [_['name'] for _ in data_api_client.req.get_domains()['domains']]


@main.route('/search/sellers')
def supplier_search():
    DOMAINS_SEARCH = feature.is_active('DOMAINS_SEARCH')

    data_api_client = DataAPIClient()

    sort_order = request.args.get('sort_order', 'asc')
    if sort_order not in ('asc', 'desc'):
        abort(400, 'Invalid sort_order: {}'.format(sort_order))
    sort_terms = request.args.getlist('sort_term')
    keyword = request.args.get('keyword', None)

    if not sort_terms:  # Sort by A-Z for default
        sort_terms = ['name']

    data_api_client = DataAPIClient()

    selected_roles = set(request.args.getlist('role'))

    if DOMAINS_SEARCH:
        roles = get_all_domains(data_api_client)
    else:
        roles, raw_role_data = get_all_roles(data_api_client)

    sidepanel_roles = [Option('role', role, role, role in selected_roles) for role in roles]
    sidepanel_filters = [
        Filter('Capabilities', sidepanel_roles),
    ]

    sort_queries = []

    allowed_sort_terms = set(('name',))  # Limit what can be sorted

    for sort_term in sort_terms:
        if sort_term in allowed_sort_terms:
            if sort_term == 'name':  # Use 'name' in url to keep it clean but query needs to search on not analyzed.
                sort_term = 'name.not_analyzed'

            sort_queries.append({
                sort_term: {"order": sort_order, "mode": "min"}
            })
        else:
            abort(400, 'Invalid sort_term: {}'.format(sort_term))

    if selected_roles:
        if DOMAINS_SEARCH:
            for each in selected_roles:
                if each not in roles:
                    abort(400, 'Invalid role: {}'.format(each))
            filter_terms = {"domains.assessed": list(selected_roles)}
        else:
            filters = []
            for role in selected_roles:
                if role in raw_role_data:
                    filters.extend(raw_role_data[role])
                else:
                    abort(400, 'Invalid role: {}'.format(role))
            filter_terms = {"prices.serviceRole.role": filters}

        query = {
            "query": {
                "filtered": {
                    "query": {
                        "match_all": {}
                    },
                    "filter": {
                        "terms": filter_terms,
                    }
                }
            },
            "sort": sort_queries,
            }

    elif keyword:
        query = {
            "query": {
                "match_phrase_prefix": {
                    "name": keyword
                }
            },
            "sort": sort_queries
        }
    else:
        query = {
            "query": {
                "match_all": {
                }
            },
            "sort": sort_queries
        }

    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        abort(400, 'Invalid page number: {}'.format(request.args['page']))
    results_from = (page - 1) * SUPPLIER_RESULTS_PER_PAGE

    find_suppliers_params = {
        'from': results_from,
        'size': SUPPLIER_RESULTS_PER_PAGE
    }

    response = data_api_client.find_suppliers(data=query, params=find_suppliers_params)

    results = []

    for supplier in response['hits']['hits']:
        details = supplier['_source']

        if DOMAINS_SEARCH:
            tags = [Role(d) for d in details['domains']['assessed']]
        else:
            supplier_roles = []
            seen_supplier_roles = set()
            if details.get('prices'):
                for price in details['prices']:
                    role = normalise_role(price['serviceRole']['role'])
                    if role not in seen_supplier_roles:
                        supplier_roles.append(Role(role))
                        seen_supplier_roles.add(role)
            tags = supplier_roles

        result = Result(
            details['name'],
            details['summary'],
            [],
            sorted(tags),
            url_for('.get_supplier', code=details['code']))

        results.append(result)

    num_results = response['hits']['total']
    results_to = min(num_results, page * SUPPLIER_RESULTS_PER_PAGE)

    pages = get_page_list(SUPPLIER_RESULTS_PER_PAGE, num_results, page)

    return render_template(
        'search_sellers.html',
        title='Supplier Catalogue',
        search_url=url_for('.supplier_search'),
        search_keywords='',
        sidepanel_filters=sidepanel_filters,
        num_results=num_results,
        results=results,
        results_from=results_from + 1,
        results_to=results_to,
        pages=pages,
        page=page,
        num_pages=pages[-1],
        selected_roles=selected_roles,
        sort_order=sort_order,
        sort_terms=sort_terms,
        sort_term_name_label='A to Z' if sort_order == 'asc' else 'Z to A',
        keyword=keyword,
        )
