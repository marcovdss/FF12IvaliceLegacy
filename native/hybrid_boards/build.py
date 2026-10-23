"""Build the experimental Windows x64 board core; no game files required."""
from pathlib import Path
import argparse,hashlib,json,os,subprocess
from image import mapped_image
HERE=Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--compiler',default=os.environ.get('IVALICE_LEGACY_CXX','C:/msys64/ucrt64/bin/g++.exe'))
args=parser.parse_args()
COMPILER=Path(args.compiler).resolve()
EXPECTED_COMPILER='g++.exe (Rev3, Built by MSYS2 project) 16.2.0'
OUT=HERE/'build'
OUT.mkdir(exist_ok=True)
version=subprocess.check_output([str(COMPILER),'--version'],text=True).splitlines()[0]
if version!=EXPECTED_COMPILER:
    raise RuntimeError('Compiler differs from the reviewed native prototype toolchain')
command=[str(COMPILER),'-std=c++17','-O2','-Wall','-Wextra','-Werror','-shared','-nostdlib',
         '-fno-exceptions','-fno-rtti','-fno-stack-protector','-fno-builtin','-fno-asynchronous-unwind-tables',
         '-Wl,--entry,0','-Wl,--no-insert-timestamp','-Wl,--image-base,0x180000000',
         str(HERE/'core.cpp'),'-o',str(OUT/'hybrid_core.dll')]
environment=dict(os.environ)
environment['PATH']=str(COMPILER.parent)+os.pathsep+environment.get('PATH','')
subprocess.run(command,check=True,env=environment)
flat,layout=mapped_image((OUT/'hybrid_core.dll').read_bytes())
(OUT/'hybrid_core.image').write_bytes(flat)
layout['sha256']=hashlib.sha256(flat).hexdigest()
(OUT/'image-manifest.json').write_text(json.dumps(layout,indent=2)+'\n')
manifest={'compiler':version,'command':command,'dllSha256':hashlib.sha256((OUT/'hybrid_core.dll').read_bytes()).hexdigest(),
          'sourceSha256':hashlib.sha256((HERE/'core.cpp').read_bytes()).hexdigest(),
          'buildSourceSha256':{name:hashlib.sha256((HERE/name).read_bytes()).hexdigest()
                               for name in ('build.py','image.py')},
          'imageSha256':layout['sha256'],'imageSize':len(flat),
          'purpose':'Offline native-core artifact; no hooks, loader entry or game deployment'}
(OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps(manifest,indent=2))
