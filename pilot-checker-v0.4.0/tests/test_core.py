"""Regression tests. Authored but not run until the user requests execution."""
import json
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from pilotcheck.core import classify, split_keys, bounded, resolve, read_xlsx, build_plan, merge_observations, input_value_changed
from pilotcheck.report import export
from pilotcheck.context import Document


def row(name='Search', reach='enter-submit'):
    return {'action_name': name, 'reach_path': reach}


class ClassifierTests(unittest.TestCase):
    def test_email_subscription_is_not_safe_get_navigation(self):
        for action in ['https://www.target.com/', 'https://www.target.com/s', 'https://www.bestbuy.com/cart']:
            el={'method':'get','action':action,'blocking_fields':[{'id':'email-address','name':'email-address','type':'text'}]}
            result=classify(el,row('form → '+action),[])
            self.assertEqual(result['rule_id'],'email-subscription')
            self.assertEqual(result['consequence_tier'],'unknown')
            self.assertFalse(result['probe_allowed'])

    def test_sign_up_button_is_not_a_menu(self):
        result=classify({'tag':'button','type':'button','text':'Sign up'},row('Sign up','focused-control'),[])
        self.assertFalse(result['probe_allowed'])
        self.assertEqual(result['queue'],'subscription')

    def test_buy_now_never_auto_activates(self):
        result=classify({'tag':'a','href':'https://shop.example/product/1'},row('Buy now','focused-control'),[])
        self.assertFalse(result['probe_allowed']);self.assertIsNone(result['is_danger_cell'])

    def test_privacy_landing_not_navigation(self):
        r=classify({'tag':'a','href':'https://example.com/sentry/landing?type=ca_donotsell'},row('Privacy','focused-control'),[])
        self.assertFalse(r['probe_allowed']);self.assertEqual(r['suggested_c'],'unknown')

    def test_shipping_get_form_unknown(self):
        r=classify({'method':'get','action':'https://example.com/location','blocking_fields':[{'name':'postalCode'}]},row('Confirm'),[])
        self.assertEqual(r['suggested_c'],'unknown');self.assertFalse(r['probe_allowed'])

    def test_navigation_suggestion_not_final_label(self):
        r=classify({'tag':'a','href':'https://example.com/product/1'},row('Product','focused-control'),[])
        self.assertEqual(r['suggested_c'],'C1');self.assertEqual(r['danger_status'],'unresolved')
        self.assertEqual(r['consequence_tier'],'unknown');self.assertIsNone(r['is_danger_cell'])

    def test_inline_handler_blocks_navigation_suggestion(self):
        r=classify({'tag':'a','href':'https://example.com/help'},row('Help','focused-control'),[{'aux':{'onclick_attr':'deleteAll()'}}])
        self.assertEqual(r['rule_id'],'scripted-link')

    def test_search_form_probe(self):
        r=classify({'method':'get','action':'https://example.com/search','blocking_fields':[{'name':'q'}]},row(),[])
        self.assertTrue(r['probe_allowed']);self.assertEqual(r['confirmation_tier'],'unknown')

    def test_login_not_strong_reauthentication(self):
        r=classify({'tag':'form','blocking_fields':[{'name':'email','type':'email'}]},row('Sign in'),[])
        self.assertEqual(r['confirmation_tier'],'unknown');self.assertFalse(r['probe_allowed'])

    def test_account_menu_has_explicit_probe_policy(self):
        r=classify({'tag':'button','id':'account-menu-account-button','type':'button','css_path':'#account-menu-account-button'},row('react onKeyDown on button','hotkey'),[])
        self.assertEqual(r['rule_id'],'authentication');self.assertTrue(r['probe_allowed'])
        self.assertEqual(r['suggested_c'],'unknown')

    def test_action_words_on_ordinary_links_do_not_mean_action_execution(self):
        for label, href in [('Buy now','https://shop.example/product/1'),
                            ('Share on X','https://www.x.com/BestBuy'),
                            ('Clear','https://example.com/search?filter=clear')]:
            r=classify({'tag':'a','href':href},row(label,'focused-control'),[])
            self.assertEqual(r['suggested_c'],'C1')
            self.assertFalse(r['probe_allowed'])
            self.assertEqual(r['consequence_tier'],'unknown')

    def test_delegated_callback_is_not_its_targets_href(self):
        r=classify({'tag':'a','href':'https://example.com/help','src':'e=>unknown(e)'},row('Help','hotkey'),[])
        self.assertEqual(r['suggested_c'],'unknown')
        self.assertFalse(r['probe_allowed'])

    def test_known_search_callback_can_be_observed_without_becoming_c1(self):
        el={'tag':'button','type':'button','id':'autocomplete-clear-button',
            'stored_context':{'matched':True,'attrs':{'id':'autocomplete-clear-button'}}}
        r=classify(el,row('Clear','hotkey'),[])
        self.assertTrue(r['probe_allowed'])
        self.assertEqual(r['suggested_c'],'unknown')
        self.assertEqual(r['consequence_tier'],'unknown')
        el['type']='submit'
        self.assertFalse(classify(el,row('Clear','hotkey'),[])['probe_allowed'])

    def test_search_requires_both_route_and_field_purpose(self):
        for action, name in [('https://example.com/settings','q'),('https://example.com/search','postalCode')]:
            r=classify({'method':'get','action':action,'blocking_fields':[{'name':name}]},row(),[])
            self.assertEqual(r['suggested_c'],'unknown')

    def test_generic_input_accesskey_is_not_search_focus(self):
        r=classify({'tag':'input','name':'postalCode','form_action':'https://example.com/location'},row('Address','accesskey'),[])
        self.assertEqual(r['suggested_c'],'unknown')

    def test_terms_word_alone_is_not_a_dialog(self):
        r=classify({'tag':'button','role':'button','text':'Terms and Conditions'},row('Terms and Conditions','focused-control'),[])
        self.assertEqual(r['suggested_c'],'unknown')

    def test_identical_terms_contexts_never_make_activation_target_unique(self):
        doc=Document('<div><div role="button" data-testid="hri-disclaimer-modal-link">Terms at checkout</div>'
                     '<div role="button" data-testid="hri-disclaimer-modal-link">Terms at checkout</div></div>')
        el={'tag':'div','role':'button','css_path':'div > div','text':'Terms at checkout'}
        el['stored_context']=doc.context(el)
        r=classify(el,row('Terms at checkout','focused-control'),[])
        self.assertEqual(r['suggested_c'],'C1')
        self.assertFalse(r['probe_allowed'])
        self.assertFalse(el['stored_context']['matched'])

    def test_search_landmark_must_match_stored_element_and_field(self):
        el={'method':'get','action':'https://example.com/login','blocking_fields':[{'name':'header-search','type':'search'}],
            'stored_context':{'matched':True,'attrs':{'role':'search'}}}
        self.assertEqual(classify(el,row('form → example.com/login'),[])['suggested_c'],'C1')
        el['stored_context']['matched']=False
        self.assertEqual(classify(el,row('form → example.com/login'),[])['suggested_c'],'unknown')

    def test_stored_badge_identity_distinguishes_display_from_purchase(self):
        doc=Document('<section><button title="badge for Best Buy Employee">Best Buy Employee</button></section>')
        el={'tag':'button','css_path':'section > button','text':'Best Buy Employee'}
        el['stored_context']=doc.context(el)
        r=classify(el,row('Best Buy Employee','focused-control'),[])
        self.assertEqual(r['rule_id'],'badge-description')
        self.assertEqual(r['suggested_c'],'C1')

    def test_mutating_link_routes_stay_unknown_despite_harmless_label(self):
        for href in ['https://example.com/help?action=delete','https://x.com/intent/tweet','https://example.com/subscribe']:
            r=classify({'tag':'a','href':href},row('Help','focused-control'),[])
            self.assertEqual(r['suggested_c'],'unknown')
            self.assertFalse(r['probe_allowed'])

    def test_alternative_keys_not_sequence(self):
        self.assertEqual(split_keys('Enter | Space'),['Enter','Space'])
        self.assertEqual(split_keys('Tab → Enter'),[])
        self.assertEqual(split_keys('Ctrl+Enter'),[])


class EvidenceTests(unittest.TestCase):
    def test_one_source_row_with_two_targets_never_generates_activation_jobs(self):
        r={'id':'R1','probe_allowed':True,'suggested_c':'unknown','reach_path':'hotkey','output_sequence':'Space',
           'evidence':[{'element':{'css_path':selector},'url':'https://example.com/search',
                        'frame_url':'https://example.com/search','ref':ref} for selector,ref in
                       [('#filter-one','raw/s.json#frames[0].dom.framework_handlers[0]'),
                        ('#filter-two','raw/s.json#frames[0].dom.framework_handlers[1]')]]}
        p=build_plan({'baseline_sha256':'x','records':[r]})
        self.assertEqual(p['jobs'],[])
        self.assertEqual(p['skipped'][0]['queue'],'target_identity_review')

    def test_html_context_rejects_ambiguous_and_changed_identity(self):
        d=Document('<div><a id="help" href="/help">Help</a><a href="/other">Other</a></div>')
        self.assertFalse(d.context({'css_path':'div > a'})['matched'])
        self.assertFalse(d.context({'css_path':'a#help','href':'/delete'})['matched'])
        self.assertTrue(d.context({'css_path':'div > a:nth-of-type(1)','href':'/help'})['matched'])

    def test_prior_exclusion_does_not_hide_a_reopened_unknown(self):
        r={'id':'R1','probe_allowed':False,'suggested_c':'unknown','reason':'callback unknown','queue':'effect_review',
           'previous_review':{'status':'excluded','c':'C1'}}
        p=build_plan({'baseline_sha256':'x','records':[r]})
        self.assertEqual(p['skipped'][0]['queue'],'effect_review')
    def test_input_validity_and_new_fields_do_not_imply_value_change(self):
        self.assertFalse(input_value_changed([],[{'id':'','value':''}]))
        self.assertFalse(input_value_changed([{'id':'q','value':'','invalid':False}],[{'id':'q','value':'','invalid':True}]))
        self.assertTrue(input_value_changed([{'id':'q','value':'earbuds'}],[{'id':'q','value':''}]))
    def test_pointer_and_path_guards(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'raw.json').write_text(json.dumps({'frames':[{'handlers':{'records':[{'seq':7,'src':'x'}]}}]}))
            el,_=resolve(p,'raw.json#frames[0].handlers.records[seq=7]',{})
            self.assertEqual(el['src'],'x')
            with self.assertRaises(ValueError):bounded(p,'../outside.json')
            with self.assertRaises(ValueError):resolve(p,'raw.json#frames[0]junk',{})

    def test_xlsx_shared_and_inline_strings(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'source.xlsx'
            with zipfile.ZipFile(p,'w') as z:
                z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="labels" r:id="rId1"/></sheets></workbook>')
                z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
                z.writestr('xl/sharedStrings.xml','<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>site</t></si></sst>')
                z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>example.com</t></is></c></row></sheetData></worksheet>')
            self.assertEqual(read_xlsx(p,'labels'),[{'_excel_row':2,'site':'example.com'}])

    def test_hidden_elements_not_probeable_and_previous_labels_not_reused_as_gold(self):
        r={'id':'R1','probe_allowed':True,'reach_path':'focused-control','output_sequence':'Enter',
           'rule_id':'isolated-control','previous_review':{'status':'excluded'},
           'evidence':[{'element':{'tag':'button','css_path':'#x','visible':False},'url':'https://example.com',
                        'frame_url':'https://example.com','frame_is_top':True,'ref':'raw/a.json#x','raw_sha256':'abc'}]}
        dataset={'baseline_sha256':'b','records':[r]}
        self.assertEqual(build_plan(dataset)['jobs'],[])
        self.assertEqual(build_plan(dataset,True)['jobs'],[])

    def test_safe_csv_and_html(self):
        r={'id':'R','site':'example.com','action_name':'=HYPERLINK("evil")<script>alert(1)</script>',
           'rule_id':'x','suggested_c':'unknown','queue':'review','reason':'x','probe_allowed':False}
        with tempfile.TemporaryDirectory() as td:
            out=Path(td);export({'records':[r],'summary':{'rows':1,'auto_probe_candidates':0}}, {'jobs':[]},out)
            self.assertIn("'=HYPERLINK",(out/'classification.csv').read_text(encoding='utf-8-sig'))
            self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;',(out/'report.html').read_text(encoding='utf-8'))

    def test_merge_rejects_missing_or_different_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td);(out/'summary.json').write_text(json.dumps({'plan_sha256':'wrong'}));(out/'probe_plan.json').write_text('{}')
            with self.assertRaises(ValueError):merge_observations({'baseline_sha256':'a'}, {'jobs':[]},out)
            (out/'summary.json').write_text(json.dumps({'completed':False}),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'중단된'):merge_observations({'baseline_sha256':'a'}, {'jobs':[]},out)

    def test_successful_merge_preserves_unknown_and_requires_distinct_attempts(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td);job={'job_id':'j','key':'Enter','members':['R1']};plan={'baseline_sha256':'a','jobs':[job]}
            manifest=out/'probe_plan.json';manifest.write_text(json.dumps(plan),encoding='utf-8')
            (out/'summary.json').write_text(json.dumps({'plan_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest()}),encoding='utf-8')
            observations=[{'job_id':'j','key':'Enter','status':'no_observed_change','attempt':n,'members':['R1']} for n in [1,2]]
            target=out/'probe_results.jsonl';target.write_text('\n'.join(json.dumps(x) for x in observations),encoding='utf-8')
            dataset={'baseline_sha256':'a','records':[{'id':'R1','consequence_tier':'unknown','danger_status':'unresolved'}],'summary':{}}
            merge_observations(dataset,plan,out)
            self.assertTrue(dataset['records'][0]['observations'][0]['consistent'])
            self.assertEqual(dataset['records'][0]['consequence_tier'],'unknown')
            self.assertEqual(dataset['summary']['inspection_row_outcomes'],{'no_observed_change':1})
            # Older preflight-skip logs omitted key metadata. Preserve the raw
            # log and attach only the planned condition, never a delivered key.
            target.write_text(json.dumps({'job_id':'j','status':'site_paused_after_preflight_failure','members':['R1']}),encoding='utf-8')
            merge_observations(dataset,plan,out)
            skipped=dataset['records'][0]['observations'][0]['attempts'][0]
            self.assertIn('not dispatched',skipped['key_provenance'])
            self.assertEqual(dataset['summary']['rows_with_key_input'],0)
            target.write_text(json.dumps({'job_id':'j','status':'local_ui_changed','key_dispatched':True,'members':['R1']}),encoding='utf-8')
            with self.assertRaises(ValueError):merge_observations(dataset,plan,out)
            observations[1]['attempt']=1
            target.write_text('\n'.join(json.dumps(x) for x in observations),encoding='utf-8')
            merge_observations(dataset,plan,out)
            self.assertFalse(dataset['records'][0]['observations'][0]['consistent'])


if __name__=='__main__':unittest.main()
