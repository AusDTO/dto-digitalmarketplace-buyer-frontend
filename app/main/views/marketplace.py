# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import io
import xlsxwriter

from flask_login import current_user
from flask import abort, current_app, make_response, redirect, \
    render_template, request, session, url_for, flash, Response

from dmutils.formats import DateFormatter
from dmutils.forms import DmForm, render_template_with_csrf
from react.response import from_response, validate_form_data
from react.render import render_component
from app.main.utils import get_page_list

from app import data_api_client, content_loader
from app.main import main
from app.helpers.terms_helpers import check_terms_acceptance, get_current_terms_version
from app.helpers.buyers_helpers import (
    get_framework_and_lot,
    count_unanswered_questions,
    has_permission_to_edit_brief
)

from ..forms.brief_forms import BriefSearchForm

from flask_weasyprint import render_pdf
from dmapiclient.errors import HTTPError
from app.api_client.error import APIError
import pendulum


@main.route('/')
def index():
    metrics = {}
    try:
        metrics = data_api_client.get_metrics()
    except Exception as e:
        current_app.logger.error(e)

    suppliers_count = metrics.get('supplier_count', {"value": "0"})['value']
    briefs_count = metrics.get('briefs_total', {"value": "0"})['value']
    briefs_live_count = metrics.get('briefs_live', {"value": "0"})['value']
    awarded_to_smes = metrics.get('awarded_to_smes', {"value": "0"})['value']
    total_contracted = metrics.get('total_contracted', {"value": "0"})['value']

    check_terms_acceptance()
    buyer_logged_in = False
    if current_user.is_authenticated and current_user.role == 'buyer':
        buyer_logged_in = True

    return render_template(
        'index.html',
        suppliers_count=suppliers_count,
        briefs_count=briefs_count,
        briefs_live_count=briefs_live_count,
        awarded_to_smes=awarded_to_smes,
        total_contracted=total_contracted,
        buyer_logged_in=buyer_logged_in
    )


@main.route('/<template_name>')
def content(template_name):
    if template_name == 'buyers-guide':
        return redirect('https://marketplace1.zendesk.com/hc/en-gb/categories/115001542047-Buyer-guide-and-FAQs',
                        code=301)  # 301 Moved Permanently
    if template_name == 'sellers-guide':
        return redirect('https://marketplace1.zendesk.com/hc/en-gb/categories/115001540368-Seller-guide-and-FAQs',
                        code=301)  # 301 Moved Permanently
    if template_name == 'assessment-criteria':
        return redirect('https://marketplace1.zendesk.com/hc/en-gb/articles/333757011655-Assessment-criteria',
                        code=301)
    if template_name == 'capabilities-and-rates':
        return redirect('https://marketplace1.zendesk.com/hc/en-gb/articles/360000080555-Daily-rates-trend-charts',
                        code=301)
    if template_name == 'contact-us':
        return redirect('https://marketplace1.zendesk.com/hc/en-gb/articles/360001050936',
                        code=301)
    try:
        return render_template('content/{}.html'.format(template_name))
    except:  # noqa
        abort(404)


@main.route('/terms-of-use')
def terms_of_use():
    return redirect('https://marketplace1.zendesk.com/hc/en-gb/articles/360001037036', code=301)


@main.route('/privacy-policy')
def privacy_policy():
    return redirect('https://marketplace1.zendesk.com/hc/en-gb/articles/360001027895', code=301)


@main.route('/security')
def security():
    return redirect('https://marketplace1.zendesk.com/hc/en-gb/articles/360001027915', code=301)


@main.route('/disclaimer')
def disclaimer():
    return redirect('https://marketplace1.zendesk.com/hc/en-gb/articles/360001028135', code=301)


@main.route('/copyright')
def copyright():
    return redirect('https://marketplace1.zendesk.com/hc/en-gb/articles/360001037656', code=301)


@main.route('/become-a-seller')
def becomeSeller():
    return redirect('https://marketplace1.zendesk.com/hc/en-gb/articles/115011258607', code=301)


def _is_supplier_selected_for_brief(brief):
    def domain(email):
        return email.split('@')[-1]

    current_user_domain = domain(current_user.email_address) \
        if domain(current_user.email_address) not in current_app.config.get('GENERIC_EMAIL_DOMAINS') \
        else None

    if brief.get('sellerSelector', '') == 'allSellers':
        return True
    if brief.get('sellerSelector', '') == 'someSellers':
        seller_domain_list = [domain(x).lower() for x in brief.get('sellerEmailList', [])]
        return (current_user.email_address in
                (email_address.lower() for email_address in brief.get('sellerEmailList', [])) or
                (current_user_domain.lower() in seller_domain_list if current_user_domain else False))
    if brief.get('sellerSelector', '') == 'oneSeller':
        return (current_user.email_address.lower() == brief.get('sellerEmail', '').lower() or
                (current_user_domain.lower() == domain(brief.get('sellerEmail', '').lower())
                 if current_user_domain else False))
    return False


@main.route('/<framework_slug>/opportunities/<brief_id>')
def get_brief_by_id(framework_slug, brief_id):
    briefs = data_api_client.get_brief(brief_id)
    brief = briefs.get('briefs')
    if brief['lotSlug'] in ['rfx', 'atm', 'specialist', 'training2']:
        return redirect('/2/%s/opportunities/%s' % (framework_slug, brief_id), 301)
    if brief['status'] not in ['live', 'closed']:
        if (
            not current_user.is_authenticated or
            (brief['users'] and brief['users'][0]['id'] != current_user.id) or
            current_user.id not in [tb.get('userId') for tb in brief.get('teamBriefs', [])]
        ):
            abort(404, "Opportunity '{}' can not be found".format(brief_id))

    if current_user.is_authenticated and current_user.role == 'supplier':
        brief_responses = data_api_client.find_brief_responses(
            brief_id, current_user.supplier_code)["briefResponses"]
        selected_for_brief = _is_supplier_selected_for_brief(brief)
    else:
        brief_responses = None
        selected_for_brief = False

    brief['clarificationQuestions'] = [
        dict(question, number=index+1)
        for index, question in enumerate(brief['clarificationQuestions'])
    ]

    brief_content = content_loader.get_builder(framework_slug, 'display_brief').filter(
        brief
    )

    sections = brief_content.summary(brief)
    unanswered_required, unanswered_optional = count_unanswered_questions(sections)

    brief_of_current_user = False
    if not current_user.is_anonymous and len(brief.get('users')) > 0:
        brief_of_current_user = brief['users'][0]['id'] == current_user.id

    is_restricted_brief = brief.get('sellerSelector', '') in ('someSellers', 'oneSeller')

    brief_published_date = brief['dates'].get('published_date', None)
    feature_date = current_app.config['MULTI_CANDIDATE_PUBLISHED_DATE']

    published_date = pendulum.parse(brief_published_date) if brief_published_date else feature_date.subtract(days=1)
    application_url = "/2/brief/{}/respond".format(brief['id'])
    application_specialist_url = application_url
    application_specialist_submitted_url = None

    if published_date >= feature_date:
        application_specialist_url = "/2/brief/{}/specialist/respond".format(brief['id'])
        application_specialist_submitted_url = "/2/brief/{}/specialist/respond/submitted".format(brief['id'])

    application_training_url = "/2/brief/{}/training/respond".format(brief['id'])

    add_case_study_url = None

    profile_application_status = None
    supplier = None
    unassessed_domains = {}
    assessed_domains = {}
    profile_url = None
    supplier_assessments = {}
    supplier_framework = None

    if current_user.is_authenticated:
        if current_user.supplier_code is not None:
            supplier = data_api_client.get_supplier(
                current_user.supplier_code
            ).get('supplier', None)

        profile_application_id = current_user.application_id

        if supplier is not None:
            profile_url = '/supplier/{}'.format(supplier.get('code'))
            assessed_domains = supplier.get('domains').get('assessed', None)
            unassessed_domains = supplier.get('domains').get('unassessed', None)
            legacy_domains = supplier.get('domains').get('legacy', None)

            if profile_application_id is None:
                profile_application_id = supplier.get('application_id', None)

            supplier_code = supplier.get('code')
            supplier_assessments = data_api_client.req.assessments().supplier(supplier_code).get()

            if len(legacy_domains) != 0:
                for i in range(len(legacy_domains)):
                    supplier_assessments['assessed'].append(legacy_domains[i])

            supplier_framework_ids = supplier.get('frameworks')
            for i in range(len(supplier_framework_ids)):
                if supplier.get('frameworks')[i].get('framework_id') == 7:
                    supplier_framework = 'digital-marketplace'
            if supplier_framework is None:
                supplier_framework = 'digital-service-professionals'

        if profile_application_id is not None:
            try:
                profile_application = data_api_client.req.applications(profile_application_id).get()

                if unassessed_domains is None:
                    unassessed_domains = profile_application.get(
                        'application').get('supplier').get('domains', None).get('unassessed', None)
                if assessed_domains is None:
                    assessed_domains = profile_application.get(
                        'application').get('supplier').get('domains', None).get('assessed', None)

                profile_application_status = profile_application.get('application').get('status', None)
                if profile_application.get('application').get('type') == 'edit':
                    profile_application_status = 'approved'

            except APIError:
                pass
            except HTTPError:
                pass

    domain_id = None
    if brief.get('areaOfExpertise'):
        current_domain = data_api_client.req.domain(brief['areaOfExpertise']).get()
        domain_id = current_domain['domain']['id']
    elif brief['lotSlug'] == 'training':
        domain_id = 15  # training

    return render_template_with_csrf(
        'brief.html',
        add_case_study_url=add_case_study_url,
        application_url=application_url,
        application_specialist_url=application_specialist_url,
        application_specialist_submitted_url=application_specialist_submitted_url,
        application_training_url=application_training_url,
        assessed_domains=assessed_domains,
        brief=brief,
        brief_responses=brief_responses,
        brief_of_current_user=brief_of_current_user,
        content=brief_content,
        domain_id=domain_id,
        is_restricted_brief=is_restricted_brief,
        selected_for_brief=selected_for_brief,
        profile_application_status=profile_application_status,
        profile_url=profile_url,
        unassessed_domains=unassessed_domains,
        supplier_assessments=supplier_assessments,
        supplier_framework=supplier_framework,
        unanswered_required=unanswered_required,
        training_domain_name='Training, Learning and Development'
    )


@main.route('/<framework_slug>/opportunities/<brief_id>/response')
def get_brief_response_preview_by_id(framework_slug, brief_id):
    briefs = data_api_client.get_brief(brief_id)
    brief = briefs.get('briefs')
    brief_url = url_for('main.index', _external=True) + '{}/opportunities/{}'.format(framework_slug, brief['id'])

    if brief['status'] not in ['live', 'closed']:
        if not current_user.is_authenticated or brief['users'][0]['id'] != current_user.id:
            abort(404, "Opportunity '{}' can not be found".format(brief_id))

    hypothetical_dates = brief['dates'].get('hypothetical', None)
    if hypothetical_dates is None:
        published_date = brief['dates'].get('published_date', None)
        closing_time = brief['dates'].get('closing_time', None)
    else:
        published_date = hypothetical_dates.get('published_date', None)
        closing_time = hypothetical_dates.get('closing_time', None)

    outdata = io.BytesIO()

    workbook = xlsxwriter.Workbook(outdata)
    bold_header = workbook.add_format({'bg_color': '#e8f5fa', 'bold': True, 'text_wrap':  True})
    bold_question = workbook.add_format({'bg_color': '#f3f3f3', 'valign': 'top', 'text_wrap':  True,
                                         'border': 1, 'border_color': "#AAAAAA", 'bold': True})
    bold_red = workbook.add_format({'bold': True, 'font_color': '#fc0d1b', 'text_wrap':  True})
    italic_header = workbook.add_format({'bg_color': '#e8f5fa', 'italic': True})
    italic_lightgrey = workbook.add_format({'italic': True, 'font_color': '#999999'})
    italic_darkgrey_question = workbook.add_format({'italic': True, 'font_color': '#666666', 'bg_color': '#f3f3f3',
                                                    'valign': 'top', 'text_wrap':  True,
                                                    'border': 1, 'border_color': "#AAAAAA"})
    darkgrey = workbook.add_format({'font_color': '#666666', 'text_wrap':  True})
    heading = workbook.add_format({'bold': True, 'font_size': '14', 'text_wrap':  True})
    header = workbook.add_format({'bg_color': '#e8f5fa'})
    cta = workbook.add_format({'bg_color': 'd9ead4', 'align': 'center',
                               'color': 'blue', 'underline': 1, 'text_wrap':  True})
    bold_cta = workbook.add_format({'bg_color': 'd9ead4', 'bold': True, 'align': 'center'})
    question = workbook.add_format({'bg_color': '#f3f3f3', 'valign': 'top', 'text_wrap':  True,
                                    'border': 1, 'border_color': "#AAAAAA"})
    link = workbook.add_format({'bg_color': '#e8f5fa', 'color': 'blue', 'underline': 1})
    right_border_question = workbook.add_format({'right': 1, 'right_color': 'black', 'bg_color': '#f3f3f3',
                                                 'valign': 'top', 'text_wrap':  True, 'border': 1,
                                                 'border_color': "#AAAAAA"})
    sheet = workbook.add_worksheet('Response')

    sheet.set_column('E:E', 50)
    sheet.set_column('D:D', 5)
    sheet.set_column('C:C', 50)
    sheet.set_column('B:B', 30)
    sheet.set_column('A:A', 30)

    sheet.merge_range(0, 0, 0, 2, '',  italic_header)
    sheet.write_url('A1', brief_url)
    sheet.write_rich_string('A1',  italic_header,
                            'Use this template if you are waiting to be assessed, or want to collaborate '
                            'with others, before submitting your response to this brief.\n'
                            'If you have been assessed and are ready to submit, you will need to '
                            'copy and paste your answers from this template into \n', link, brief_url)
    sheet.write_string('D1', '', right_border_question)

    df = DateFormatter(current_app.config['DM_TIMEZONE'])
    sheet.write_string('E1', brief['title'], heading)
    sheet.write_string('E2', brief['summary'], darkgrey)
    sheet.write_string('E3', 'For: '+brief['organisation'], darkgrey)
    sheet.write_string('E4', 'Published: '+df.dateformat(published_date), darkgrey)
    sheet.write_string('E5', 'Closing date for application: ' +
                       df.datetimeformat(closing_time), bold_red)

    sheet.write_string('A2', 'Guidance', bold_question)
    sheet.write_string('B2', 'Question', bold_question)
    sheet.write_string('C2', 'Answer', bold_question)
    sheet.write_string('D2', '', right_border_question)
    sheet.write_string('A3', '')
    sheet.write_string('B3', 'Essential selection criteria', bold_header)
    sheet.write_string('D3', '', right_border_question)

    b_count = 4
    e_start = 2
    for essential in brief['essentialRequirements']:
        sheet.write_string('D'+str(b_count), '', right_border_question)
        if brief['lotSlug'] == 'digital-professionals':
            sheet.write_string('B'+str(b_count),  essential,  question)
        else:
            sheet.write_string('B'+str(b_count),  essential['criteria'],  question)
        sheet.write_string('C'+str(b_count), '')
        sheet.write_string('D'+str(b_count), '', right_border_question)
        b_count += 1
    sheet.write_string('A'+str(b_count), '')
    sheet.write_string('D'+str(b_count), '', right_border_question)
    sheet.write_string('B'+str(b_count), 'Desirable selection criteria', bold_header)
    b_count += 1

    for nice in brief['niceToHaveRequirements']:
        if brief['lotSlug'] == 'digital-professionals':
            sheet.write_string('B'+str(b_count), nice, question)
        else:
            sheet.write_string('B'+str(b_count), nice['criteria'], question)
        sheet.write_string('C'+str(b_count), '')
        sheet.write_string('D'+str(b_count), '', right_border_question)
        b_count += 1

    sheet.merge_range(
        e_start,
        0,
        b_count - 2,
        0,
        'Skills and experience\n'
        'As a guide to answering the skills and experience criteria, '
        'you could explain:\n'
        '- What the situation was\n'
        '- The work the specialist or team completed\n'
        '- What the results were \n'
        'You can reuse examples if you wish. \n'
        '\n'
        'You must have all essential skills and experience '
        'to apply for this opportunity.\n'
        '150 words max ',
        italic_darkgrey_question
    )

    sheet.write_string('A'+str(b_count), '')
    sheet.write_string('D'+str(b_count), '', right_border_question)
    sheet.write_string('B'+str(b_count), '')
    b_count += 1

    if brief['lotSlug'] == 'digital-professionals':
        sheet.write_string('A'+str(b_count), '')
        sheet.write_string('D'+str(b_count), '', right_border_question)
        sheet.write_string('B'+str(b_count), "When can you start?", bold_question)
        b_count += 1

        sheet.write_string('A'+str(b_count), '')
        sheet.write_string('D'+str(b_count), '', right_border_question)
        sheet.write_string('B'+str(b_count), "What is your daily rate, including GST?", bold_question)
        b_count += 1
        sheet.write_string('A'+str(b_count), '')
        sheet.write_string('D'+str(b_count), '', right_border_question)
        sheet.write_rich_string('B'+str(b_count), bold_question, "Attach a résumé\n",
                                question, "Use an Open Document Format (ODF) or PDF/A (eg. .pdf, .odt). "
                                          "The maximum file size of each document is 5MB. "
                                          "You can upload a maximum of 3 candidate CVs.", question)
        b_count += 1

    if brief['lotSlug'] == 'specialist':
        sheet.write_string('A'+str(b_count), '')
        sheet.write_string('D'+str(b_count), '', right_border_question)
        sheet.write_rich_string(
            'B'+str(b_count),
            bold_question, "When can you start?\n",
            question, '{} has requested {}'.format(brief['organisation'], df.dateformat(brief['startDate'])),
            question
        )

        b_count += 1

        sheet.write_string('A'+str(b_count), '')
        sheet.write_string('D'+str(b_count), '', right_border_question)
        preferred_format_for_rates = brief['preferredFormatForRates']
        if preferred_format_for_rates == 'dailyRate':
            sheet.write_string('B'+str(b_count), "What is your daily rate, excluding GST?", bold_question)
        elif preferred_format_for_rates == 'hourlyRate':
            sheet.write_string('B'+str(b_count), "What is your hourly rate, excluding GST?", bold_question)
        b_count += 1

        if brief['securityClearance'] == 'mustHave':
            security_clearance_label = ''
            if brief['securityClearanceCurrent'] == 'baseline':
                security_clearance_label = 'baseline'
            elif brief['securityClearanceCurrent'] == 'nv1':
                security_clearance_label = 'negative vetting level 1'
            elif brief['securityClearanceCurrent'] == 'nv2':
                security_clearance_label = 'negative vetting level 1'
            elif brief['securityClearanceCurrent'] == 'pv':
                security_clearance_label = 'positive vetting'
            sheet.write_string('A'+str(b_count), '')
            sheet.write_string('D'+str(b_count), '', right_border_question)
            sheet.write_string('C'+str(b_count), 'Yes/No')
            sheet.write_string(
                'B'+str(b_count),
                "Do you have a {} security clearance?".format(security_clearance_label),
                bold_question
            )
            b_count += 1

        sheet.write_string('A'+str(b_count), '')
        sheet.write_string('D'+str(b_count), '', right_border_question)
        sheet.write_string('C'+str(b_count), 'Yes/No')
        sheet.write_string('B'+str(b_count), "What is your eligibility to work in Australia?", bold_question)
        b_count += 1

        sheet.write_string('A'+str(b_count), '')
        sheet.write_string('D'+str(b_count), '', right_border_question)
        sheet.write_string('C'+str(b_count), 'Yes/No')
        sheet.write_string(
            'B'+str(b_count),
            "Have you previously worked for {}?".format(brief['organisation']),
            bold_question
        )
        b_count += 1

        sheet.write_string('A'+str(b_count), '')
        sheet.write_string('D'+str(b_count), '', right_border_question)
        sheet.write_rich_string('B'+str(b_count), bold_question, "Attach a résumé\n",
                                question, "Use an Open Document Format (ODF) or PDF/A (eg. .pdf, .odt). "
                                          "The maximum file size of each document is 5MB. ", question)
        b_count += 1
        sheet.write_string('A'+str(b_count), '')
        sheet.write_string('D'+str(b_count), '', right_border_question)
        sheet.write_rich_string('B'+str(b_count), bold_question, "Other documents (optional)\n",
                                question, "Use an Open Document Format (ODF) or PDF/A (eg. .pdf, .odt). "
                                          "The maximum file size of each document is 5MB. "
                                          "If requested by the buyer, you can upload additional documents", question)
        b_count += 1

    sheet.write_string('A'+str(b_count), '')
    sheet.write_string('D'+str(b_count), '', right_border_question)
    sheet.write_string('B'+str(b_count), '', question)
    b_count += 1

    sheet.write_string('A'+str(b_count), "All communication about your application will be sent to this address",
                       italic_darkgrey_question)
    sheet.write_string('D'+str(b_count), '', right_border_question)
    sheet.write_string('B'+str(b_count), "Contact email:", bold_question)
    b_count += 1

    sheet.write_string('A'+str(b_count), '')
    sheet.write_string('B'+str(b_count), '')
    sheet.write_string('C'+str(b_count), '')
    sheet.write_string('D'+str(b_count), '')
    b_count += 1

    sheet.write_string('C'+str(b_count), 'Ready to apply?', bold_cta)
    b_count += 1

    sheet.write_url('C'+str(b_count), brief_url, cta, brief_url)
    workbook.close()

    return Response(
        outdata.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={
            "Content-Disposition": "attachment;filename=brief-response-template-{}.xlsx".format(brief['id']),
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        }
    ), 200


@main.route('/<framework_slug>/opportunities')
def list_opportunities(framework_slug):
    return redirect('/2/opportunities', 301)


@main.route('/collaborate')
def collaborate():
    return redirect('/2/collaborate', 301)


@main.route('/collaborate/code')
def collaborate_code():
    rendered_component = render_component('bundles/Collaborate/CollaborateCodeWidget.js', {})
    return render_template(
        '_react.html',
        breadcrumb_items=[
            {'link': url_for('main.index'), 'label': 'Home'},
            {'link': url_for('main.collaborate'), 'label': 'Collaborate'},
            {'label': 'Open source library'}
        ],
        component=rendered_component,
        main_class='collapse'
    )


@main.route('/collaborate/project/new')
def collaborate_create_project():
    form = DmForm()
    basename = url_for('.collaborate_create_project')
    props = {
        'form_options': {
            'csrf_token': form.csrf_token.current_token
        },
        'project': {
        },
        'basename': basename
    }

    if 'project' in session:
        props['projectForm'] = session['project']

    rendered_component = render_component('bundles/Collaborate/ProjectFormWidget.js', props)

    return render_template(
        '_react.html',
        breadcrumb_items=[
            {'link': url_for('main.index'), 'label': 'Home'},
            {'link': url_for('main.collaborate'), 'label': 'Collaborate'},
            {'label': 'Add project'}
        ],
        component=rendered_component
    )


@main.route('/collaborate/project/new', methods=['POST'])
def collaborate_create_project_submit():
    project = from_response(request)

    fields = ['title', 'client', 'stage']

    basename = url_for('.collaborate_create_project')
    errors = validate_form_data(project, fields)
    if errors:
        form = DmForm()
        rendered_component = render_component('bundles/Collaborate/ProjectFormWidget.js', {
            'form_options': {
                'csrf_token': form.csrf_token.current_token,
                'errors': errors
            },
            'projectForm': project,
            'basename': basename
        })

        return render_template(
            '_react.html',
            breadcrumb_items=[
                {'link': url_for('main.index'), 'label': 'Home'},
                {'link': url_for('main.collaborate'), 'label': 'Collaborate'},
                {'label': 'Add project'}
            ],
            component=rendered_component
        )

    try:
        project = data_api_client.req.projects().post(data={'project': project})['project']

        rendered_component = render_component('bundles/Collaborate/ProjectSubmitConfirmationWidget.js', {})

        return render_template(
            '_react.html',
            breadcrumb_items=[
                {'link': url_for('main.index'), 'label': 'Home'},
                {'link': url_for('main.collaborate'), 'label': 'Collaborate'},
                {'label': 'Add project'}
            ],
            component=rendered_component
        )

    except APIError as e:
        form = DmForm()
        flash('', 'error')
        rendered_component = render_component('bundles/Collaborate/ProjectFormWidget.js', {
            'form_options': {
                'csrf_token': form.csrf_token.current_token
            },
            'projectForm': project,
            'basename': basename
        })

        return render_template(
            '_react.html',
            breadcrumb_items=[
                {'link': url_for('main.index'), 'label': 'Home'},
                {'link': url_for('main.collaborate'), 'label': 'Collaborate'},
                {'label': 'Add project'}
            ],
            component=rendered_component
        )


@main.route('/collaborate/project/<int:id>')
def collaborate_view_project(id):
    project = data_api_client.req.projects(id).get()['project']
    if project.get('status', '') != 'published':
        abort(404)
    rendered_component = render_component('bundles/Collaborate/ProjectViewWidget.js', {'project': project})
    return render_template(
        '_react.html',
        breadcrumb_items=[
            {'link': url_for('main.index'), 'label': 'Home'},
            {'link': url_for('main.collaborate'), 'label': 'Collaborate'},
            {'label': project['title']}
        ],
        component=rendered_component,
        main_class='collapse'
    )


@main.route('/buyers/frameworks/<framework_slug>/requirements/<lot_slug>', methods=['GET'])
def info_page_for_starting_a_brief(framework_slug, lot_slug):
    if lot_slug in ['digital-outcome', 'digital-professionals']:
        abort(404)
    framework, lot = get_framework_and_lot(framework_slug, lot_slug, data_api_client,
                                           status='live', must_allow_brief=True)
    return render_template(
        "buyers/start_brief_info.html",
        framework=framework,
        lot=lot
    ), 200
