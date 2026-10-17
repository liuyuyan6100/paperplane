"""Exercise the actual editor over a loopback-only server and temporary data."""
import base64, io, json, subprocess, tempfile, threading, unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from studio.server import Server
from studio.service import Studio
from studio.db import dumps

class ArticleFlowBrowserTests(unittest.TestCase):
    def test_editor_next_step_cancel_and_draft_flow(self):
        with tempfile.TemporaryDirectory(prefix='paperplane-article-browser-') as directory:
            root=Path(directory); app=Studio(root/'data'); server=Server(('127.0.0.1',0),app)
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                app.config.update({'WECHAT_APP_ID':'synthetic','WECHAT_APP_SECRET':'synthetic','AI_MODEL':'synthetic-body-model','AI_BASE_URL':'http://127.0.0.1:1/v1','AI_MODE':'api'})
                with app.db.connect() as db: db.execute("UPDATE channels SET enabled=1,config=? WHERE id='wechat'",(dumps({'mode':'api'}),))
                image=io.BytesIO(); Image.new('RGB',(64,40),'#e6dbbd').save(image,format='PNG')
                cover=app.upload({'data':base64.b64encode(image.getvalue()).decode(),'title':'合成封面'})['url']
                post=app.save_post({'title':'隔离验收：从本地推理开始','body':'## 理解模型\n\n这是合成文章，使用独立数据验证流程。\n\n## 动手验证\n\n保存正文，确认图文与来源。','summary':'','author':'合成作者','cover':cover,'stage':'draft'})
                missing=app.save_post({'title':'只缺封面的隔离文章','body':'## 概念\n\n合成说明。','summary':'摘要完整','author':'作者','stage':'draft'})
                output=Path(__file__).resolve().parents[2]/'work/test-results/article-flow-browser'; output.mkdir(parents=True,exist_ok=True)
                fixture=root/'fixture.json'; fixture.write_text(json.dumps({'url':f'http://127.0.0.1:{server.server_port}','post_id':post['id'],'missing_id':missing['id'],'output':str(output)},ensure_ascii=False),encoding='utf-8')
                with patch('studio.wechat.send_draft',side_effect=AssertionError('No platform send in browser test')), patch('studio.adapters.request_json',side_effect=AssertionError('No model calls in browser test')):
                    result=subprocess.run(['node',str(Path(__file__).with_suffix('.cjs')),str(fixture)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
                self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
                self.assertEqual(len(app.jobs()),1)
                self.assertEqual(app.jobs()[0]['status'],'queued')
            finally: server.shutdown(); server.server_close(); app.close()

if __name__=='__main__': unittest.main()
