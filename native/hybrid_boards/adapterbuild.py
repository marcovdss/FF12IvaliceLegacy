"""Build the experimental native routing layer; never deploys to a game."""
from pathlib import Path
import argparse,hashlib,json,os,subprocess
from image import mapped_image
HERE=Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--compiler',default=os.environ.get('IVALICE_LEGACY_CXX','C:/msys64/ucrt64/bin/g++.exe'))
args=parser.parse_args();compiler=Path(args.compiler).resolve()
version=subprocess.check_output([str(compiler),'--version'],text=True).splitlines()[0]
if version!='g++.exe (Rev3, Built by MSYS2 project) 16.2.0':raise RuntimeError('Unreviewed compiler')
out=HERE/'build';out.mkdir(exist_ok=True)
command=[str(compiler),'-std=c++17','-O2','-Wall','-Wextra','-Werror','-shared','-nostdlib',
         '-fno-exceptions','-fno-rtti','-fno-stack-protector','-fno-builtin','-fno-asynchronous-unwind-tables',
         '-Wl,--entry,0','-Wl,--no-insert-timestamp','-Wl,--image-base,0x180000000',
         str(HERE/'adapter.cpp'),'-o',str(out/'hybrid_adapter.dll')]
env=dict(os.environ);env['PATH']=str(compiler.parent)+os.pathsep+env.get('PATH','')
subprocess.run(command,check=True,env=env)
# Adapter adds exports to the core; reuse strict PE checks with the expected count.
import image
raw=(out/'hybrid_adapter.dll').read_bytes()
flat,layout=image.mapped_image(raw,expected_exports=18)
(out/'hybrid_adapter.image').write_bytes(flat)
layout['sha256']=hashlib.sha256(flat).hexdigest()
(out/'adapter-image-manifest.json').write_text(json.dumps(layout,indent=2)+'\n')
manifest={'compiler':version,'command':command,'dllSha256':hashlib.sha256(raw).hexdigest(),
          'sourceSha256':{name:hashlib.sha256((HERE/name).read_bytes()).hexdigest()
                          for name in ('adapter.cpp','core.cpp','adapterbuild.py','image.py','thunks.py','sites.py')},
          'imageSha256':layout['sha256'],'purpose':'Offline routing layer; no live installer'}
(out/'adapter-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps(manifest,indent=2))
