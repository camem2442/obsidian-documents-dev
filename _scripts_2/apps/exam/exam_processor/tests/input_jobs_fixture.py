"""Synthetic source PDF and fixed AI responses for input-to-batch acceptance."""
from pathlib import Path


def source_pdf(path):
    stream=b'0.5 w 30 700 m 580 700 l S 306 700 m 306 110 l S\nBT /F1 12 Tf 40 665 Td (1. Synthetic first question?) Tj 0 -22 Td (A first choice / B second choice) Tj ET\nBT /F1 12 Tf 320 665 Td (2. Synthetic second question?) Tj 0 -22 Td (A first choice / B second choice) Tj ET'
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
             b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
             b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    data=b'%PDF-1.4\n';offsets=[]
    for i,obj in enumerate(objects,1):offsets.append(len(data));data+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    offset=len(data);data+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets)
    data+=b'trailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n'+str(offset).encode()+b'\n%%EOF\n'
    Path(path).write_bytes(data)


def processor(store,jid,rid,revision,operation,**kwargs):
    from ..pipeline.ai import process
    payload={'body':'Synthetic transcription fixture','diagrams':[]} if operation=='extract' else {'result':'no_difference','issues':[]}
    return process(store,jid,rid,revision,operation,caller=lambda _: {'data':payload,'provider':'synthetic_input_fixture','configured_model':'fixed'},**kwargs)


if __name__=='__main__':
    import argparse,time
    import uvicorn
    from ..server import create_app
    from ..storage.store import Store
    from ..storage.input_jobs import InputJobs
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--port',type=int,default=0);parser.add_argument('--delay',type=float,default=.4);parser.add_argument('--fail-once',action='store_true')
    args=parser.parse_args();root=Path(args.root);root.mkdir(parents=True,exist_ok=True)
    from .. import config
    config.KS_ROOT=root
    source=root/'fixture.pdf'
    if not source.exists():source_pdf(source)
    original=InputJobs.create;failed=[False]
    def delayed(self,e,a):
        time.sleep(args.delay)
        if args.fail_once and e['input']['source']=='Synthetic fail once' and not failed[0]:
            failed[0]=True;raise ValueError('명시 합성 파서 실패 (fixture)')
        return original(self,e,a)
    InputJobs.create=delayed
    uvicorn.run(create_app(Store(root/'data', root/'exports'),batch_processor=processor),host='127.0.0.1',port=args.port)
