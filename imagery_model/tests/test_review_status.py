import re
import subprocess
from urban_tree_ml.model_debug import MODEL_DEBUG_HTML
from urban_tree_ml.review_gallery import GALLERY_HTML
from urban_tree_ml.review_status import inject_review_status_options, REVIEW_STATUSES


def test_completion_options_shared_and_filter_includes_first_pass():
    for html in (MODEL_DEBUG_HTML, GALLERY_HTML):
        rendered = inject_review_status_options(html)
        for _, label in REVIEW_STATUSES:
            assert f'>{label}</option>' in rendered
    function = 'function matchesReviewStatus(status){' + MODEL_DEBUG_HTML.split('function matchesReviewStatus(status){', 1)[1].split('function validationQueue()', 1)[0]
    js = "let value='not-final';const $=()=>({value});\n" + function + '''
const states=[undefined,{reviewed:true},{reviewed:true,more_done:true}];
for(const [filter,expected] of Object.entries({all:[true,true,true],pending:[true,false,false],'not-final':[true,true,false],'second-pending':[false,true,false],done:[false,true,true],'more-done':[false,false,true]})){
 value=filter;if(JSON.stringify(states.map(matchesReviewStatus))!==JSON.stringify(expected))throw Error(filter);
}
'''
    subprocess.run(['node', '-e', js], check=True, capture_output=True, text=True)
    for script in re.findall(r'<script>(.*?)</script>', MODEL_DEBUG_HTML, re.S):
        subprocess.run(['node', '--check'], input=script, check=True, capture_output=True, text=True)
