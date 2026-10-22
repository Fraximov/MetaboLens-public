"""The August split review board: stable automatic queue, evidence, explicit edits."""
import math
import dash
from dash import Input, Output, State, dcc, html, dash_table
import dash_bootstrap_components as dbc
from services import workflow_service
from services.api_client import APIError
from services.page_cache import build_page_cache, page_cache_data, page_cache_matches
from ui.components import REVIEW_TABLE_STYLE
from ui.figures import build_eic_figure, build_ms2_mirror_figure


def _bundle(value):
    if not value:
        raise ValueError('Select a preview bundle')
    _, job, preview = value.split(':')
    return int(job), int(preview)


def review_view():
    decisions = [{'label': x.title(), 'value': x} for x in ('automatic','include','exclude')]
    return html.Div([
        dcc.Store(id='fr-page'), dcc.Store(id='fr-action'), dcc.Store(id='fr-editor'),
        dcc.Store(id='fr-selection', storage_type='session'),
        dcc.Store(id='fr-native-key'),
        dcc.ConfirmDialog(id='fr-copy-confirm', message='Replace this feature’s existing annotation-specific override with the statistics override?'),
        html.H5('Feature filter review'),
        html.P('Review the automatic decisions, inspect the evidence, then save your overrides. Automatic decisions remain unchanged.', className='text-muted'),
        dcc.Dropdown(id='fr-bundle', placeholder='Select a preview bundle', clearable=False),
        html.Div(id='fr-state', className='my-2'),
        dbc.Row([
            dbc.Col([
                html.Label('Review domain'),
                dcc.Dropdown(id='fr-domain', options=[{'label':'Statistics','value':'statistics'}, {'label':'Annotation','value':'annotation'}], value='statistics', clearable=False,persistence=True,persistence_type='session'),
                html.Label('Queue', className='mt-2'),
                dcc.Dropdown(id='fr-queue', options=[{'label': label,'value': value} for value,label in (
                    ('include','Automatically included'),('review','Needs review'),('exclude','Automatically excluded'),('manual','Manually changed'),('all','All features'))], value='include',clearable=False,persistence=True,persistence_type='session'),
                dbc.Input(id='fr-search', placeholder='Search name, source ID, m/z, RT or QC reason', debounce=True, className='my-2',persistence=True,persistence_type='session'),
                dbc.Row([
                    dbc.Col(dcc.Dropdown(id='fr-effective', options=[{'label':x.title(),'value':x} for x in ('include','review','exclude')], placeholder='Effective decision',value='',persistence=True,persistence_type='session')),
                    dbc.Col(dcc.Dropdown(id='fr-ms2',options=[{'label':'With MS2','value':'yes'},{'label':'Without MS2','value':'no'}],placeholder='MS2 evidence',value='',persistence=True,persistence_type='session')),
                ],className='mb-2'),
                dash_table.DataTable(id='fr-table', data=[], columns=[{'name':label,'id':key} for key,label in (
                    ('source_feature_id','Source ID'),('metabolite_name','Feature'),('automatic_statistics','Statistics auto'),
                    ('effective_statistics','Statistics now'),('automatic_annotation','Annotation auto'),('effective_annotation','Annotation now'),('manual_badge','Edited'))],
                    page_action='custom', page_current=0, page_size=25, page_count=0,
                    cell_selectable=True, persistence=True, persisted_props=['page_current'], persistence_type='session',
                    **REVIEW_TABLE_STYLE,
                    style_data_conditional=[{'if':{'filter_query':'{manually_changed} = true'}, 'backgroundColor':'#eef4ff'}]),
                html.Div(id='fr-count',className='small text-muted my-2'),
                dbc.Button('Reload saved decisions', id='fr-reload', n_clicks=0, size='sm', outline=True),
            ],md=5),
            dbc.Col([
                dbc.ButtonGroup([dbc.Button('Previous',id='fr-prev',n_clicks=0,outline=True),dbc.Button('Next',id='fr-next',n_clicks=0,outline=True)],size='sm',className='mb-2'),
                html.Div(id='fr-nav-status',className='small text-muted'),
                html.Div(id='fr-evidence'),
                html.Div(id='fr-native-evidence'),
                html.Label('Override for the selected review domain'),
                dcc.Dropdown(id='fr-value',options=decisions,value='automatic',clearable=False),
                html.Div(dbc.Checkbox(id='fr-copy',label='Apply the same override to annotation',value=True),id='fr-copy-wrap',className='my-2'),
                dbc.Textarea(id='fr-comment',placeholder='Comment (optional)',maxLength=4000,className='my-2'),
                dbc.Button('Save decision',id='fr-save',n_clicks=0,color='primary',disabled=True),
            ],md=7),
        ]),
        html.Div(id='fr-action-status',className='my-3'),
        dbc.Button('Freeze saved decisions and finalize',id='fr-continue',n_clicks=0,color='success',disabled=True),
        html.P('Only saved decisions are frozen. Finalization reuses the engine and preview results.',className='small text-muted mt-2'),
    ])


def register_callbacks(app):
    @app.callback(Output('fr-bundle','options'),Output('fr-bundle','value'),
                  Input('jw-outputs-store','data'),State('url','pathname'),State('fr-bundle','value'))
    def review_bundles(store, pathname, current):
        from callbacks.job_workspace import parse_job_route
        job, section = parse_job_route(pathname)
        if section != 'review' or (store or {}).get('job_id') != job:
            return [], None
        options = [{'label':f"Preview · file {r['file_id']}",'value':f"bundle:{job}:{r['file_id']}"}
                   for r in store.get('outputs',[]) if r.get('artifact_role')=='metabolensr_preview_manifest']
        values = [o['value'] for o in options]
        return options, current if current in values else (values[0] if values else None)

    @app.callback(Output('fr-page','data'),Output('fr-table','page_current'),Input('fr-bundle','value'),Input('fr-queue','value'),
                  Input('fr-domain','value'),Input('fr-search','value'),Input('fr-effective','value'),Input('fr-ms2','value'),
                  Input('fr-table','page_current'),Input('fr-reload','n_clicks'),Input('fr-action','data'),
                  State('token-store','data'),State('assay-select-store','data'))
    def load_review_page(bundle, queue, domain, query, effective, ms2, page, _reload, action, token, assay):
        if not bundle or not token or not assay:
            return None,dash.no_update
        if action and not action.get('ok') and dash.ctx.triggered_id == 'fr-action':
            return dash.no_update,dash.no_update
        try:
            result = workflow_service.get_feature_review(token,assay,*_bundle(bundle),queue=queue,domain=domain,
                        query=query,effective=effective or None,has_ms2={'yes':True,'no':False}.get(ms2),page=(page or 0)+1)
            return {**result,'bundle':bundle},result.get('page',(page or 0)+1)-1
        except (APIError,ValueError) as exc:
            return {'bundle':bundle,'error':str(exc),'items':[]},dash.no_update

    @app.callback(Output('fr-table','data'),Output('fr-table','page_count'),Output('fr-count','children'),
                  Output('fr-state','children'),Output('fr-continue','disabled'),Output('fr-continue','children'),Input('fr-page','data'))
    def render_review_page(data):
        data=data or {}
        if data.get('error'):
            return [],0,'',dbc.Alert(data['error'],color='danger'),True,'Freeze saved decisions and finalize'
        revision=data.get('revision',0)
        status=f"Saved draft · revision {revision}"
        if data.get('decision_source') == 'final':
            status=f"Showing final decisions · saved draft revision {revision}"
        if data.get('frozen_revision') is not None:
            status += f" · frozen revision {data['frozen_revision']}"
        if not data.get('can_edit'):
            status += ' · read-only (editing is available at the feature-review checkpoint)'
        total=data.get('total',0)
        count=f"{total} features · page {data.get('page',1)}. Queue membership follows automatic decisions."
        if not total:
            count += ' Try Needs review or All features.'
        return ([{**r,'id':r['feature_id'],'manual_badge':'Edited' if r.get('manually_changed') else ''} for r in data.get('items',[])],
                max(1,math.ceil(total/data.get('page_size',25))),count,status,not (data.get('can_continue') or data.get('can_retry')),
                f"Retry frozen revision {data.get('frozen_revision')}" if data.get('can_retry') else 'Freeze saved decisions and finalize')

    @app.callback(Output('fr-selection','data'),Output('fr-nav-status','children'),
                  Input('fr-table','active_cell'),Input('fr-prev','n_clicks'),Input('fr-next','n_clicks'),Input('fr-page','data'),
                  State('fr-selection','data'),State('fr-table','page_current'))
    def select_review_feature(cell, _prev, _next, data, selected, page):
        if not data or not data.get('bundle'):
            return dash.no_update,dash.no_update
        data=data or {}; rows=data.get('items',[]); bundle=data.get('bundle')
        identities=[r['feature_id'] for r in rows]
        selected=selected or {}
        trigger=dash.ctx.triggered_id
        if not identities:
            return None,dash.no_update
        identity=selected.get('feature_id') if selected.get('bundle')==bundle else None
        if trigger=='fr-table' and cell:
            identity=cell.get('row_id') or identities[min(cell.get('row',0),len(identities)-1)]
        elif trigger in {'fr-prev','fr-next'}:
            position=identities.index(identity) if identity in identities else 0
            position += 1 if trigger=='fr-next' else -1
            if position >= len(identities) and (page or 0)+1 < math.ceil(data.get('total',0)/data.get('page_size',25)):
                return dash.no_update,'Use the table’s next-page control to continue.'
            if position < 0 and (page or 0)>0:
                return dash.no_update,'Use the table’s previous-page control to continue.'
            identity=identities[max(0,min(position,len(identities)-1))]
        if identity not in identities:
            identity=identities[-1] if selected.get('bundle')==bundle and selected.get('edge')=='last' else identities[0]
        return {'bundle':bundle,'feature_id':identity},''

    @app.callback(Output('fr-evidence','children'),Output('fr-value','value'),Output('fr-comment','value'),
                  Output('fr-editor','data'),Output('fr-copy-wrap','style'),
                  Output('fr-native-evidence','children'),Output('fr-native-key','data'),
                  Input('fr-selection','data'),Input('fr-page','data'),Input('fr-domain','value'),
                  State('token-store','data'),State('assay-select-store','data'),State('fr-native-key','data'),
                  State('fr-reload','n_clicks'))
    def review_evidence(selected,data,domain,token,assay,native_key=None,reload_generation=0):
        selected=selected or {}; data=data or {}
        row=next((r for r in data.get('items',[]) if r['feature_id']==selected.get('feature_id')),None)
        style={} if domain=='statistics' else {'display':'none'}
        if not row or selected.get('bundle')!=data.get('bundle'):
            return 'Select a feature.','automatic','',None,style,[],None
        ms2 = {True:'available',False:'unavailable',None:'unknown'}[row.get('has_ms2')]
        children=[html.H6(row['label']),html.P(f"Adduct: {row.get('adduct_type') or 'unknown'} · MS2: {ms2}",className='small text-muted'),
                  html.P(f"Statistics: {row.get('automatic_statistics')} → {row.get('effective_statistics')} · Annotation: {row.get('automatic_annotation')} → {row.get('effective_annotation')}"),
                  html.Details([html.Summary('QC evidence and reasons'),html.Ul([html.Li(f'{key}: {value or "unavailable"}') for key,value in row.get('qc',{}).items()])])]
        native_children, next_key = dash.no_update, dash.no_update
        # Native evidence is fixed for this bundle/feature. Only the small identity
        # is stored; saved decisions and their revision always come from this page.
        # Reload invalidates it after the page refresh, including missing evidence
        # that may have become available since the previous successful request.
        identity = {'bundle':data['bundle'],'feature_id':row['feature_id']}
        if not (page_cache_matches(native_key,token,assay_id=assay) and page_cache_data(native_key)==identity
                and native_key.get('reload',0)==(reload_generation or 0)):
            native_children = []
            try:
                payload=workflow_service.get_package_feature_trace(token,assay,*_bundle(data['bundle']),row['feature_id'])
                if payload.get('spot'):
                    native_children.append(dcc.Graph(figure=build_eic_figure(payload).update_layout(height=310,title=None),config={'responsive':True}))
                else:
                    native_children.append(html.P(payload.get('evidence_message') or 'Chromatogram unavailable.'))
                if payload.get('ms2'):
                    native_children.append(dcc.Graph(figure=build_ms2_mirror_figure(payload).update_layout(height=280),config={'responsive':True}))
                next_key = {**build_page_cache(identity,token,assay_id=assay),'reload':reload_generation or 0}
            except (APIError,ValueError) as exc:
                native_children.append(html.P(f'Evidence unavailable: {exc}'))
                next_key = None
        return (children,row.get(f'override_{domain}','automatic'),row.get('comment',''),
                {**selected,'domain':domain,'revision':data['revision']},style,native_children,next_key)

    @app.callback(Output('fr-save','disabled'),Input('fr-selection','data'),Input('fr-page','data'),
                  Input('fr-domain','value'),Input('fr-editor','data'))
    def gate_review_save(selected,data,domain,editor):
        return not (data and data.get('can_edit') and selected and selected.get('bundle')==data.get('bundle')
                    and editor=={**selected,'domain':domain,'revision':data['revision']})

    @app.callback(Output('fr-action','data'),Output('fr-copy-confirm','displayed'),
                  Input('fr-save','n_clicks'),Input('fr-copy-confirm','submit_n_clicks'),
                  State('fr-page','data'),State('fr-selection','data'),State('fr-domain','value'),State('fr-value','value'),
                  State('fr-copy','value'),State('fr-comment','value'),State('token-store','data'),State('assay-select-store','data'),State('fr-editor','data'),prevent_initial_call=True)
    def save_review(clicks, confirmed, data, selected, domain, value, copy, comment, token, assay, editor=None):
        if not clicks or not data or not selected or not data.get('can_edit') or selected.get('bundle')!=data.get('bundle'):
            return dash.no_update,False
        if editor != {**selected,'domain':domain,'revision':data['revision']}:
            return {'ok':False,'message':'Wait for the selected feature’s editor to finish loading.'},False
        row=next((r for r in data['items'] if r['feature_id']==selected.get('feature_id')),None)
        if not row:
            return dash.no_update,False
        replace=dash.ctx.triggered_id=='fr-copy-confirm' and bool(confirmed)
        if domain=='statistics' and copy and row.get('annotation_override_source')=='annotation' and row.get('override_annotation','automatic')!='automatic' and not replace:
            return dash.no_update,True
        change={'feature_id':selected['feature_id'],'domain':domain,'value':value,'comment':comment or '',
                'copy_to_annotation':bool(copy and domain=='statistics'),'confirm_annotation_replace':replace}
        try:
            result=workflow_service.patch_feature_review(token,assay,*_bundle(data['bundle']),expected_revision=data['revision'],changes=[change])
            return {'ok':True,'message':f"Saved revision {result['revision']}",**result},False
        except (APIError,ValueError) as exc:
            return {'ok':False,'message':f'{exc} Reload saved decisions before trying again.'},False

    @app.callback(Output('fr-action-status','children'),Input('fr-action','data'))
    def review_action_status(action):
        return dbc.Alert(action['message'],color='success' if action['ok'] else 'danger') if action else ''

    @app.callback(Output('jw-continue-result','data',allow_duplicate=True),Output('fr-action','data',allow_duplicate=True),
                  Input('fr-continue','n_clicks'),State('fr-page','data'),State('token-store','data'),State('assay-select-store','data'),prevent_initial_call=True)
    def continue_review(clicks,data,token,assay):
        if not clicks or not data or not (data.get('can_continue') or data.get('can_retry')):
            return dash.no_update,dash.no_update
        job,preview=_bundle(data['bundle'])
        try:
            if data.get('can_retry'):
                workflow_service.continue_workflow_job(token,assay,job)
                return {'job_id':job,'ok':True,'new_job_id':job},{'ok':True,'message':f"Retry queued using frozen revision {data['frozen_revision']}. Newer drafts are not applied."}
            workflow_service.continue_workflow_job(token,assay,job,preview_file_id=preview,expected_revision=data['revision'])
            return {'job_id':job,'ok':True,'new_job_id':job},{'ok':True,'message':f"Frozen revision {data['revision']}; finalization queued."}
        except (APIError,ValueError) as exc:
            return dash.no_update,{'ok':False,'message':str(exc)}
